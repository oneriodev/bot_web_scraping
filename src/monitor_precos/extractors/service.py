"""Orquestra a extração: fila do banco → extrator → validação Pydantic → banco.

Não conhece Gemini nem DuckDB em detalhe: recebe um BaseExtractor e um Repository.
Por isso dá para testar com um extrator falso, sem gastar nenhuma chamada de API.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from monitor_precos.extractors.base import BaseExtractor, merge_outputs
from monitor_precos.extractors.pdf import load_pages
from monitor_precos.extractors.schemas import ExtractedOffer
from monitor_precos.storage.repository import Repository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtractionSummary:
    processed: int = 0
    extracted: int = 0  # imagens que passaram direto
    review: int = 0  # imagens que precisam de revisão humana
    errors: int = 0
    offers: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def validate_items(
    items: list[dict[str, Any]],
    page_valid_from: date | None,
    page_valid_until: date | None,
) -> tuple[list[ExtractedOffer], list[str]]:
    """Valida cada item SEPARADAMENTE: um item ruim não descarta os outros.

    Se o card não traz validade impressa, usa a validade que a página anunciou.
    """
    offers: list[ExtractedOffer] = []
    problems: list[str] = []
    for index, item in enumerate(items):
        try:
            offer = ExtractedOffer.model_validate(item)
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            problems.append(f"item {index}: {field} — {first['msg']}")
            continue
        if offer.valid_until is None and page_valid_until is not None:
            offer.valid_from, offer.valid_until = page_valid_from, page_valid_until
        offers.append(offer)
    return offers, problems


def decide_status(
    is_offer_image: bool,
    offers: list[ExtractedOffer],
    problems: list[str],
    min_confidence: float,
) -> str:
    """'extraido' quando dá para confiar; 'revisao' quando um humano precisa olhar."""
    if not is_offer_image and not offers and not problems:
        return "extraido"  # não é encarte (logo, aviso): nada a extrair, e está certo
    if problems or not offers:
        return "revisao"
    if any(offer.confidence < min_confidence for offer in offers):
        return "revisao"
    return "extraido"


class ExtractionService:
    def __init__(
        self,
        repo: Repository,
        extractor: BaseExtractor,
        *,
        min_confidence: float,
        delay_seconds: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repo = repo
        self._extractor = extractor
        self._min_confidence = min_confidence
        self._delay = delay_seconds
        self._sleep = sleep

    def run(
        self,
        *,
        competitor: str | None = None,
        limit: int | None = None,
        include_errors: bool = False,
    ) -> ExtractionSummary:
        summary = ExtractionSummary()
        queue = self._repo.collections_to_extract(
            competitor, include_errors=include_errors, limit=limit
        )
        logger.info("Fila de extração: %d arquivo(s) com %s", len(queue), self._extractor.engine)

        for index, collection in enumerate(queue):
            if index:
                self._sleep(self._delay)  # respeita o limite de requisições por minuto
            summary.processed += 1
            name = collection.local_path.name

            try:
                # Imagem = 1 página; PDF = 1 imagem por página. Cada página vai ao extrator
                # e as ofertas de todas as páginas são juntadas numa resposta só.
                pages = load_pages(collection.local_path, collection.content_type)
                outputs = []
                for page_number, (data, mime_type) in enumerate(pages):
                    if page_number:
                        self._sleep(self._delay)
                    outputs.append(self._extractor.extract(data, mime_type))
                output = merge_outputs(outputs)
            except Exception as error:
                # Um arquivo com erro (API fora, arquivo sumido...) não para a fila.
                # Console: uma linha com o motivo. Arquivo de log: traceback completo (DEBUG).
                reason = f"{type(error).__name__}: {str(error)[:200]}"
                logger.error(
                    "Falha na extração de %s — %s",
                    name,
                    reason,
                    extra={"collection_id": collection.id, "file": name},
                )
                logger.debug("Detalhes da falha em %s", name, exc_info=True)
                self._repo.set_extraction_status(collection.id, "erro")
                summary.errors += 1
                continue

            summary.input_tokens += output.input_tokens
            summary.output_tokens += output.output_tokens

            offers, problems = validate_items(
                output.items, collection.valid_from, collection.valid_until
            )
            status = decide_status(output.is_offer_image, offers, problems, self._min_confidence)
            self._repo.save_offers(
                collection.id, offers, engine=self._extractor.engine, collection_status=status
            )

            if problems:
                logger.warning(
                    "%s: %d item(ns) inválido(s) na resposta",
                    name,
                    len(problems),
                    extra={"file": name, "problems": problems},
                )
            logger.info(
                "%s: %d oferta(s), status %s",
                name,
                len(offers),
                status,
                extra={"file": name, "status": status, "offers": len(offers),
                       "tokens_in": output.input_tokens, "tokens_out": output.output_tokens},
            )  # fmt: skip

            summary.offers += len(offers)
            if status == "revisao":
                summary.review += 1
            else:
                summary.extracted += 1

        return summary
