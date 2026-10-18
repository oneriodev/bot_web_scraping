"""Importação de encartes enviados pela tela (upload no Streamlit).

Para concorrentes cujo site não permite coleta automática (ex.: Assaí, com proteção
anti-bot). O usuário baixa o encarte no próprio navegador e envia aqui. A partir
daí o caminho é o mesmo dos coletores: arquivo em data/raw/, registro em `coletas`
(com hash: nada é importado duas vezes) e execução registrada em `execucoes`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import PurePath

from monitor_precos.collectors.base import CollectedFile, compute_sha256
from monitor_precos.collectors.manual_inbox import EXTENSION_TO_TYPE, ManualInboxCollector
from monitor_precos.config import Settings
from monitor_precos.storage.repository import Repository

logger = logging.getLogger(__name__)

MAX_FILE_MB = 25

# Sugestões para a tela (os que já existem no banco também aparecem).
KNOWN_COMPETITORS = ("assai", "atacadao", "big_lar", "comper", "fort_atacadista", "serve_mais")


@dataclass(frozen=True, slots=True)
class UploadedFile:
    """Um arquivo recebido pela tela: nome original + conteúdo em bytes."""

    name: str
    content: bytes


@dataclass(slots=True)
class UploadReport:
    inserted: list[str] = field(default_factory=list)  # nomes originais importados
    duplicates: list[str] = field(default_factory=list)  # mesmo conteúdo já estava no banco
    rejected: list[str] = field(default_factory=list)  # "arquivo: motivo"


def slugify(text: str) -> str:
    """ "Fort Atacadista" → "fort_atacadista"; "Assaí" → "assai"."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def content_matches_extension(suffix: str, content: bytes) -> bool:
    """Confere a ASSINATURA do arquivo (primeiros bytes): extensão pode ser falsificada."""
    if suffix == ".pdf":
        return content.startswith(b"%PDF")
    if suffix == ".png":
        return content.startswith(b"\x89PNG")
    if suffix in (".jpg", ".jpeg"):
        return content.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _rejection(file: UploadedFile) -> str | None:
    """Motivo para recusar o arquivo, ou None se estiver tudo certo."""
    suffix = PurePath(file.name).suffix.lower()
    if suffix not in EXTENSION_TO_TYPE:
        return f"{file.name}: formato não suportado (use PDF, JPG, PNG ou WEBP)"
    if len(file.content) > MAX_FILE_MB * 1024 * 1024:
        return f"{file.name}: maior que {MAX_FILE_MB} MB"
    if not content_matches_extension(suffix, file.content):
        return f"{file.name}: o conteúdo não é um {suffix[1:].upper()} de verdade"
    return None


def _status(saved: int, rejected: int) -> str:
    if rejected and not saved:
        return "falha"
    return "parcial" if rejected else "sucesso"


def import_uploads(
    settings: Settings,
    repo: Repository,
    competitor: str,
    files: list[UploadedFile],
    *,
    campaign: str | None = None,
    valid_from: date | None = None,
    valid_until: date | None = None,
) -> UploadReport:
    """Valida, grava em disco e registra no banco os arquivos enviados pela tela."""
    collector = ManualInboxCollector(settings, competitor)  # também valida o slug
    report = UploadReport()
    run_id = repo.start_run(competitor)
    now = datetime.now(UTC)

    try:
        collected: list[CollectedFile] = []
        for file in files:
            if problem := _rejection(file):
                report.rejected.append(problem)
                continue
            suffix = PurePath(file.name).suffix.lower()
            prefix = compute_sha256(file.content)[:8]  # nomes iguais nunca se sobrescrevem
            collected.append(
                collector.save_file(
                    file.content,
                    f"{prefix}_{file.name}",
                    f"upload://{competitor}/{file.name}",
                    EXTENSION_TO_TYPE[suffix],
                    now,
                    campaign=campaign,
                    valid_from=valid_from,
                    valid_until=valid_until,
                )
            )
        result = repo.save_collections(collected)
    except Exception as error:
        logger.exception("Importação por upload falhou", extra={"competitor": competitor})
        repo.finish_run(run_id, "falha", message=f"{type(error).__name__}: {error}")
        raise

    prefix_len = len(f"upload://{competitor}/")
    for file, stored_path in result.duplicates:
        # O banco já tinha esse conteúdo: apaga só a cópia NOVA (nunca a original).
        if file.local_path.resolve() != stored_path.resolve():
            file.local_path.unlink(missing_ok=True)
        report.duplicates.append(file.source_url[prefix_len:])
    report.inserted = [file.source_url[prefix_len:] for file in result.inserted]

    repo.finish_run(
        run_id,
        _status(len(collected), len(report.rejected)),
        inserted=len(report.inserted),
        duplicates=len(report.duplicates),
        failures=len(report.rejected),
        message="; ".join(report.rejected) or None,
    )
    logger.info(
        "Importação por upload concluída",
        extra={
            "competitor": competitor,
            "inserted": len(report.inserted),
            "duplicates": len(report.duplicates),
            "rejected": len(report.rejected),
        },
    )
    return report
