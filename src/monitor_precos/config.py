"""Configuração central do projeto.

Lê variáveis do arquivo .env (via python-dotenv) e do ambiente, com valores padrão
seguros. Diferente do settings.py antigo, importar este módulo NÃO cria pastas:
isso só acontece quando alguém chama `ensure_directories()` explicitamente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from requests.utils import default_user_agent

# Raiz do repositório: src/monitor_precos/config.py → sobe 3 níveis.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
    timezone: str = "America/Cuiaba"  # fuso da operação: define o que é "hoje"

    # ---- Extração com LLM (Etapa 4)
    # repr=False: a chave NUNCA aparece se alguém fizer print(settings) ou logar o objeto.
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = "gemini-2.5-flash"
    llm_delay_seconds: float = 4.0  # pausa entre chamadas ao LLM
    extraction_min_confidence: float = 0.8  # abaixo disso, vai para revisão humana
    extraction_max_per_run: int = 20  # teto de imagens por execução (controle de custo)

    @property
    def tz(self) -> ZoneInfo:
        """Fuso horário como objeto (o banco guarda UTC; "hoje" é no fuso da loja)."""
        return ZoneInfo(self.timezone)

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
        timezone=os.getenv("TIMEZONE", "America/Cuiaba"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        llm_delay_seconds=float(os.getenv("LLM_DELAY_SECONDS", "4")),
        extraction_min_confidence=float(os.getenv("EXTRACTION_MIN_CONFIDENCE", "0.8")),
        extraction_max_per_run=int(os.getenv("EXTRACTION_MAX_PER_RUN", "20")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Atalho com cache: lê o .env só uma vez por execução."""
    return load_settings()
