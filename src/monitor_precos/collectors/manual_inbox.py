"""Coletor da caixa de entrada manual.

Para encartes recebidos por WhatsApp ou redes sociais (onde não automatizamos a
coleta): você salva as imagens/PDFs em

    data/inbox/<concorrente>/      ex.: data/inbox/assai/encarte_semana.jpg

e este coletor os importa para data/raw/<concorrente>/<data>/, igual aos coletores
de site. Depois de importado, o arquivo vai para data/inbox/<concorrente>/processados/,
assim rodar de novo não importa o mesmo arquivo duas vezes.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from monitor_precos.collectors.base import BaseCollector, CollectedFile, ContentType
from monitor_precos.config import Settings

logger = logging.getLogger(__name__)

PROCESSED_DIR_NAME = "processados"

EXTENSION_TO_TYPE: dict[str, ContentType] = {
    ".jpg": ContentType.IMAGE,
    ".jpeg": ContentType.IMAGE,
    ".png": ContentType.IMAGE,
    ".webp": ContentType.IMAGE,
    ".pdf": ContentType.PDF,
}

_VALID_SLUG = re.compile(r"^[a-z0-9_]+$")


class ManualInboxCollector(BaseCollector):
    """Importa arquivos colocados à mão em data/inbox/<concorrente>/."""

    competitor_name = "Caixa de entrada manual"

    def __init__(self, settings: Settings, competitor_slug: str) -> None:
        if not _VALID_SLUG.match(competitor_slug):
            raise ValueError(
                f"Concorrente inválido: {competitor_slug!r}. Use minúsculas, números e _ "
                "(ex.: assai, fort_atacadista)."
            )
        super().__init__(settings)
        # Aqui o concorrente vem de fora: um mesmo coletor atende vários concorrentes.
        self.competitor_slug = competitor_slug

    @property
    def inbox(self) -> Path:
        return self.settings.inbox_dir / self.competitor_slug

    def collect(self) -> list[CollectedFile]:
        self.inbox.mkdir(parents=True, exist_ok=True)
        processed_dir = self.inbox / PROCESSED_DIR_NAME
        collected: list[CollectedFile] = []
        now = datetime.now(UTC)

        for path in sorted(self.inbox.iterdir()):
            content_type = EXTENSION_TO_TYPE.get(path.suffix.lower())
            if not path.is_file() or content_type is None:
                continue  # ignora subpastas (como processados/) e arquivos não suportados

            file = self.save_file(
                path.read_bytes(),
                path.name,
                f"inbox://{self.competitor_slug}/{path.name}",
                content_type,
                collected_at=now,
            )
            collected.append(file)

            processed_dir.mkdir(exist_ok=True)
            shutil.move(path, processed_dir / path.name)

        logger.info(
            "Importação manual concluída",
            extra={"competitor": self.competitor_slug, "saved": len(collected)},
        )
        return collected
