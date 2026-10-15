"""Avaliação dos extratores contra um GABARITO preenchido à mão.

Comparar OCR com LLM só mostra se os dois CONCORDAM. Para saber quem ACERTA, é
preciso a resposta certa (ground truth): você confere alguns cards na imagem e
anota os valores verdadeiros em data/avaliacao/gabarito.csv.

O LLM é avaliado pelo que JÁ está no banco (sem nova chamada paga); o OCR roda
na hora, localmente e de graça, e também tem o tempo medido.
"""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

import duckdb

from monitor_precos.extractors.base import BaseExtractor
from monitor_precos.extractors.pdf import load_pages
from monitor_precos.extractors.schemas import parse_brl
from monitor_precos.extractors.service import validate_items

logger = logging.getLogger(__name__)

GROUND_TRUTH_COLUMNS = ["arquivo", "produto", "preco_regular", "preco_promocional"]


@dataclass(frozen=True, slots=True)
class OfferView:
    """O mínimo de uma oferta para comparar, venha do banco (LLM) ou do OCR."""

    product: str
    regular_price: Decimal | None
    promo_price: Decimal


@dataclass(frozen=True, slots=True)
class GroundTruthRow:
    filename: str
    product: str
    regular_price: Decimal | None
    promo_price: Decimal


@dataclass(frozen=True, slots=True)
class Score:
    promo_ok: bool  # preço "por" exatamente igual ao gabarito
    regular_ok: bool  # preço "de" igual (inclusive os dois vazios)
    product_similarity: float  # 0 a 1: quão parecido o nome ficou


@dataclass(frozen=True, slots=True)
class EvaluationRow:
    truth: GroundTruthRow
    llm: Score
    ocr: Score
    ocr_seconds: float


# ------------------------------------------------------------------ gabarito (CSV)
def write_template(path: Path, filenames: list[str]) -> None:
    """Cria o gabarito EM BRANCO: você preenche olhando a imagem, sem ver a resposta do LLM
    (ver a resposta antes te faria tender a concordar com ela: viés de ancoragem)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")  # ";" abre direto no LibreOffice/Excel em pt-BR
        writer.writerow(GROUND_TRUTH_COLUMNS)
        for name in filenames:
            writer.writerow([name, "", "", ""])


def load_ground_truth(path: Path) -> list[GroundTruthRow]:
    """Lê o gabarito, ignorando linhas ainda não preenchidas (sem preço "por").

    Aceita ";" ou "," como separador: o LibreOffice/Excel pode salvar com qualquer um.
    """
    rows: list[GroundTruthRow] = []
    # utf-8-sig: aceita o arquivo com ou sem o "BOM" que o Excel/LibreOffice às vezes grava.
    with path.open(encoding="utf-8-sig", newline="") as file:
        header = file.readline()
        delimiter = ";" if header.count(";") >= header.count(",") else ","
        file.seek(0)  # volta ao início para o DictReader ler o cabeçalho de novo
        for line in csv.DictReader(file, delimiter=delimiter):
            promo = parse_brl(line.get("preco_promocional"))
            if promo is None:
                continue  # linha vazia ou ainda não preenchida
            rows.append(
                GroundTruthRow(
                    filename=(line.get("arquivo") or "").strip(),
                    product=(line.get("produto") or "").strip(),
                    regular_price=parse_brl(line.get("preco_regular")),
                    promo_price=promo,
                )
            )
    return rows


# ------------------------------------------------------------------ métricas
def similarity(a: str, b: str) -> float:
    """Similaridade de texto de 0 a 1 (na Etapa 6 trocaremos pelo rapidfuzz)."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def score(offers: list[OfferView], truth: GroundTruthRow) -> Score:
    """Compara a MELHOR oferta extraída daquele card com o gabarito."""
    if not offers:
        return Score(promo_ok=False, regular_ok=False, product_similarity=0.0)
    best = max(
        offers,
        key=lambda offer: (offer.promo_price == truth.promo_price, similarity(offer.product, truth.product)),
    )  # fmt: skip
    return Score(
        promo_ok=best.promo_price == truth.promo_price,
        regular_ok=best.regular_price == truth.regular_price,
        product_similarity=round(similarity(best.product, truth.product), 2),
    )


def summarize(results: list[EvaluationRow]) -> dict[str, dict[str, float]]:
    """Taxas de acerto por motor: fração de cards com preço "por"/"de" corretos etc."""
    total = len(results) or 1
    summary: dict[str, dict[str, float]] = {}
    for engine in ("llm", "ocr"):
        scores = [getattr(row, engine) for row in results]
        summary[engine] = {
            "preco_por": sum(s.promo_ok for s in scores) / total,
            "preco_de": sum(s.regular_ok for s in scores) / total,
            "similaridade_produto": sum(s.product_similarity for s in scores) / total,
        }
    return summary


# ------------------------------------------------------------------ banco (só leitura)
def sample_filenames(connection: duckdb.DuckDBPyConnection, amount: int) -> list[str]:
    """Sorteia imagens já extraídas pelo LLM para compor o gabarito."""
    rows = connection.execute(
        "SELECT caminho_arquivo FROM coletas "
        "WHERE tipo_conteudo = 'image' AND status_extracao IN ('extraido', 'revisao') "
        f"ORDER BY random() LIMIT {int(amount)}"
    ).fetchall()
    return [Path(row[0]).name for row in rows]


def _llm_offers(
    connection: duckdb.DuckDBPyConnection, data_dir: Path, filename: str
) -> tuple[Path | None, str | None, list[OfferView]]:
    row = connection.execute(
        "SELECT id, caminho_arquivo, tipo_conteudo FROM coletas "
        "WHERE ends_with(caminho_arquivo, ?) ORDER BY id DESC LIMIT 1",
        [f"/{filename}"],
    ).fetchone()
    if row is None:
        return None, None, []
    collection_id, stored, content_type = row
    offers = connection.execute(
        "SELECT produto, preco_regular, preco_promocional FROM ofertas_extraidas "
        "WHERE coleta_id = ? ORDER BY id",
        [collection_id],
    ).fetchall()
    path = Path(stored) if Path(stored).is_absolute() else data_dir / stored
    return path, content_type, [OfferView(*offer) for offer in offers]


def evaluate(
    connection: duckdb.DuckDBPyConnection,
    data_dir: Path,
    ocr: BaseExtractor,
    truth_rows: list[GroundTruthRow],
) -> list[EvaluationRow]:
    results: list[EvaluationRow] = []
    for truth in truth_rows:
        path, content_type, llm_offers = _llm_offers(connection, data_dir, truth.filename)
        if path is None or content_type is None or not path.exists():
            logger.warning("Arquivo do gabarito não encontrado: %s", truth.filename)
            continue

        start = time.perf_counter()  # relógio de alta precisão, ideal para medir duração
        ocr_items = [item for data, mime in load_pages(path, content_type)
                     for item in ocr.extract(data, mime).items]  # fmt: skip
        seconds = time.perf_counter() - start

        ocr_valid, _ = validate_items(ocr_items, None, None)
        ocr_offers = [OfferView(o.product, o.regular_price, o.promo_price) for o in ocr_valid]
        results.append(
            EvaluationRow(
                truth=truth,
                llm=score(llm_offers, truth),
                ocr=score(ocr_offers, truth),
                ocr_seconds=seconds,
            )
        )
    return results


def write_report(path: Path, results: list[EvaluationRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(
            ["arquivo", "produto_gabarito", "preco_gabarito",
             "llm_preco_ok", "llm_de_ok", "llm_similaridade",
             "ocr_preco_ok", "ocr_de_ok", "ocr_similaridade", "ocr_segundos"]
        )  # fmt: skip
        for row in results:
            writer.writerow(
                [row.truth.filename, row.truth.product, row.truth.promo_price,
                 row.llm.promo_ok, row.llm.regular_ok, row.llm.product_similarity,
                 row.ocr.promo_ok, row.ocr.regular_ok, row.ocr.product_similarity,
                 round(row.ocr_seconds, 2)]
            )  # fmt: skip
