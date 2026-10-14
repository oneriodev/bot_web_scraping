"""Configuração de logging — feita UMA vez, no início do programa.

- Console: formato legível para humanos.
- Arquivo: uma linha JSON por evento (logging estruturado), fácil de filtrar com
  `jq`, carregar no pandas/DuckDB ou enviar a um serviço de logs no futuro.

Nos outros módulos, use apenas:
    logger = logging.getLogger(__name__)
e passe dados extras com `extra=`:
    logger.info("Imagem salva", extra={"competitor": "serve_mais", "sha256": h})
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Atributos que todo LogRecord já tem; o que sobrar veio do `extra=` e vai para o JSON.
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Transforma cada registro de log em uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Campos personalizados passados via extra={...}
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """Configura o logger raiz com console + arquivo JSON rotacionado diariamente."""
    root = logging.getLogger()
    if getattr(root, "_monitor_precos_configured", False):
        return  # idempotente: chamar duas vezes não duplica handlers

    root.setLevel(logging.DEBUG)  # o filtro real fica em cada handler

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", "%H:%M:%S")
    )
    root.addHandler(console)

    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        log_dir / "monitor_precos.jsonl", when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    # Bibliotecas de terceiros falam demais em DEBUG; deixamos só avisos delas.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    root._monitor_precos_configured = True  # type: ignore[attr-defined]
