"""Interface comum a todos os coletores.

Todo coletor (site de encarte, e-commerce, pasta de entrada manual) herda de
`BaseCollector` e implementa `collect()`, devolvendo uma lista de `CollectedFile`.
O restante do pipeline (extração, banco, painel) só conhece esse formato e nunca
precisa saber de onde o arquivo veio.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from monitor_precos.config import Settings


class ContentType(StrEnum):
    """Tipo do arquivo coletado — decide qual extrator vai processá-lo."""

    IMAGE = "image"
    PDF = "pdf"
    HTML = "html"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class CollectedFile:
    """Um arquivo coletado + metadados. Vira uma linha da tabela `coletas` na Etapa 2."""

    competitor: str  # slug do concorrente, ex.: "serve_mais"
    source_url: str  # de onde veio (URL ou "inbox://nome_do_arquivo")
    local_path: Path  # onde foi salvo
    content_type: ContentType
    sha256: str  # "impressão digital" do conteúdo, usada para não processar duplicados
    collected_at: datetime  # sempre em UTC
    # Metadados opcionais que a própria página informa (nem toda fonte tem):
    campaign: str | None = None  # ex.: "Tabloides-fecha-mes-out26-FORMATO"
    valid_from: date | None = None  # início da validade anunciada na página
    valid_until: date | None = None  # fim da validade anunciada na página


def compute_sha256(content: bytes) -> str:
    """Hash SHA-256: mesmo conteúdo → mesmo hash, mesmo que o nome do arquivo mude."""
    return hashlib.sha256(content).hexdigest()


def safe_filename(name: str) -> str:
    """Remove acentos e caracteres problemáticos (inclusive "/" e "..") de nomes de arquivo."""
    # NFKD separa "ã" em "a" + "~"; depois descartamos o que não é ASCII (os acentos).
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^\w.\-]+", "_", ascii_name, flags=re.ASCII).strip("._")
    return cleaned or "arquivo"


class BaseCollector(ABC):
    """Classe base abstrata: não pode ser instanciada, só herdada.

    Cada subclasse define `competitor_slug` e `competitor_name` e implementa `collect()`.
    """

    competitor_slug: ClassVar[str]
    competitor_name: ClassVar[str]

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # URLs já coletadas antes (vem do banco): o coletor pode pular o download.
        self.known_urls: set[str] = set()
        # Resultado da última coleta, além dos arquivos salvos:
        self.skipped_count = 0  # pulados porque a URL já estava no banco
        self.missing_urls: list[str] = []  # 404: a página cita, mas o arquivo não existe mais
        # Falhas de verdade (timeout, erro 5xx...). Não param a coleta, mas a execução
        # é registrada como "parcial" para ninguém ser enganado.
        self.failed_urls: list[str] = []

    @abstractmethod
    def collect(self) -> list[CollectedFile]:
        """Coleta os arquivos da fonte e devolve a lista do que foi salvo."""

    # ------------------------------------------------------------------ utilitários
    def run_directory(self, when: datetime) -> Path:
        """Pasta da coleta: data/raw/<concorrente>/<AAAA-MM-DD>/ — evita sobrescrever
        encartes antigos que reutilizam o mesmo nome de arquivo."""
        directory = self.settings.raw_dir / self.competitor_slug / when.strftime("%Y-%m-%d")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save_file(
        self,
        content: bytes,
        filename: str,
        source_url: str,
        content_type: ContentType,
        collected_at: datetime | None = None,
        *,
        campaign: str | None = None,
        valid_from: date | None = None,
        valid_until: date | None = None,
    ) -> CollectedFile:
        """Grava o conteúdo em disco e devolve o CollectedFile com o hash calculado."""
        collected_at = collected_at or datetime.now(UTC)
        path = self.run_directory(collected_at) / safe_filename(filename)
        path.write_bytes(content)
        return CollectedFile(
            competitor=self.competitor_slug,
            source_url=source_url,
            local_path=path,
            content_type=content_type,
            sha256=compute_sha256(content),
            collected_at=collected_at,
            campaign=campaign,
            valid_from=valid_from,
            valid_until=valid_until,
        )
