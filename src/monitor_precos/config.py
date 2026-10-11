"""Configuração central do projeto.

Lê variáveis do arquivo .env (via python-dotenv) e do ambiente, com valores padrão
seguros. Diferente do settings.py antigo, importar este módulo NÃO cria pastas:
isso só acontece quando alguém chama `ensure_directories()` explicitamente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from requests.utils import default_user_agent

# Raiz do repositório: src/monitor_precos/config.py → sobe 3 níveis.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# User-Agent padrão do requests (ex.: "python-requests/2.34.2"): não revela quem coleta
# e não finge ser navegador. Pode ser trocado pela variável USER_AGENT no .env.
DEFAULT_USER_AGENT = default_user_agent()


def _resolve_path(raw: str) -> Path:
    """Caminhos relativos no .env são relativos à raiz do projeto, não à pasta atual."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    """Configurações imutáveis (frozen=True) — ninguém altera por acidente no meio da execução."""

    data_dir: Path
    log_dir: Path
    log_level: str
    user_agent: str
    request_timeout_seconds: float
    request_delay_seconds: float
    max_retries: int

    @property
    def raw_dir(self) -> Path:
        """Arquivos brutos coletados: data/raw/<concorrente>/<data>/."""
        return self.data_dir / "raw"

    @property
    def database_path(self) -> Path:
        """Arquivo do banco analítico DuckDB."""
        return self.data_dir / "monitor_precos.duckdb"

    @property
    def inbox_dir(self) -> Path:
        """Caixa de entrada manual (imagens recebidas por WhatsApp/redes sociais)."""
        return self.data_dir / "inbox"

    def ensure_directories(self) -> None:
        """Cria as pastas de trabalho. Chamado uma vez no início da execução."""
        for directory in (self.data_dir, self.raw_dir, self.inbox_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)


def load_settings(env_file: Path | None = None) -> Settings:
    """Monta um Settings a partir do .env e das variáveis de ambiente.

    Variáveis já definidas no ambiente têm prioridade sobre o .env (override=False),
    o que facilita testes e o agendamento via cron.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    return Settings(
        data_dir=_resolve_path(os.getenv("DATA_DIR", "data")),
        log_dir=_resolve_path(os.getenv("LOG_DIR", "logs")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        user_agent=os.getenv("USER_AGENT", DEFAULT_USER_AGENT),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")),
        request_delay_seconds=float(os.getenv("REQUEST_DELAY_SECONDS", "2.0")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Atalho com cache: lê o .env só uma vez por execução."""
    return load_settings()
