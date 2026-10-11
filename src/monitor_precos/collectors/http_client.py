"""Cliente HTTP "educado", compartilhado por todos os coletores.

Concentra num lugar só as regras de coleta responsável:
    1. Identifica o bot com um User-Agent honesto (com link para o projeto).
    2. Consulta o robots.txt de cada domínio antes de acessar qualquer URL.
    3. Espaça as requisições ao mesmo domínio (rate limit).
    4. Tenta de novo, com espera crescente, em falhas temporárias (429/5xx),
       respeitando o cabeçalho Retry-After quando o servidor informa.

Assim nenhum coletor precisa (nem pode esquecer de) reimplementar essas regras.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from monitor_precos.config import Settings

logger = logging.getLogger(__name__)


class RobotsDisallowedError(Exception):
    """Levantada quando o robots.txt do site proíbe acessar a URL."""


class RateLimiter:
    """Garante um intervalo mínimo entre requisições ao MESMO domínio.

    `clock` e `sleep` são injetáveis para os testes não precisarem esperar de verdade.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._last_request_at: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        last = self._last_request_at.get(domain)
        if last is not None:
            elapsed = self._clock() - last
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at[domain] = self._clock()


class RobotsPolicy:
    """Baixa e guarda em cache o robots.txt de cada domínio.

    Regra usada (a mesma da biblioteca padrão do Python):
      - robots.txt retornou 200       → seguir as regras dele;
      - retornou 401/403 (proibido)   → tudo bloqueado;
      - outro 4xx (não existe)        → tudo liberado;
      - erro de rede ou 5xx           → por prudência, tudo bloqueado nesta execução.
    """

    def __init__(self, user_agent: str, fetch_robots: Callable[[str], tuple[int, str]]) -> None:
        self._user_agent = user_agent
        self._fetch_robots = fetch_robots  # recebe a URL do robots.txt → (status, texto)
        self._parsers: dict[str, RobotFileParser] = {}

    def _parser_for(self, base_url: str) -> RobotFileParser:
        if base_url not in self._parsers:
            parser = RobotFileParser()
            robots_url = f"{base_url}/robots.txt"
            try:
                status, text = self._fetch_robots(robots_url)
            except requests.RequestException as error:
                logger.warning(
                    "robots.txt inacessível, bloqueando domínio",
                    extra={"robots_url": robots_url, "error": str(error)},
                )
                status, text = 503, ""

            if status == 200:
                parser.parse(text.splitlines())
            elif status in (401, 403):
                # Acesso negado ao robots.txt = o site não quer robôs: bloqueamos tudo.
                parser.disallow_all = True
            elif 400 <= status < 500:
                parser.allow_all = True  # 404 e afins: robots.txt não existe
            else:
                parser.disallow_all = True
            self._parsers[base_url] = parser
        return self._parsers[base_url]

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        return self._parser_for(f"{parts.scheme}://{parts.netloc}").can_fetch(self._user_agent, url)


class PoliteHttpClient:
    """Sessão HTTP com User-Agent identificado, robots.txt, rate limit e retry."""

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._timeout = settings.request_timeout_seconds
        self._session = session or self._build_session(settings)
        self._session.headers["User-Agent"] = settings.user_agent
        self._rate_limiter = rate_limiter or RateLimiter(settings.request_delay_seconds)
        self._robots = RobotsPolicy(settings.user_agent, self._fetch_robots)

    @staticmethod
    def _build_session(settings: Settings) -> requests.Session:
        """Sessão reaproveita a conexão TCP entre requisições: mais rápida e leve p/ o site."""
        retry = Retry(
            total=settings.max_retries,
            backoff_factor=1.0,  # espera 0s, 2s, 4s... entre tentativas
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
            respect_retry_after_header=True,
        )
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _fetch_robots(self, robots_url: str) -> tuple[int, str]:
        domain = urlsplit(robots_url).netloc
        self._rate_limiter.wait(domain)
        response = self._session.get(robots_url, timeout=self._timeout)
        return response.status_code, response.text

    def get(self, url: str) -> requests.Response:
        """GET respeitando robots.txt e rate limit. Levanta exceção em erro HTTP."""
        if not self._robots.can_fetch(url):
            raise RobotsDisallowedError(f"robots.txt não permite acessar: {url}")

        self._rate_limiter.wait(urlsplit(url).netloc)
        logger.debug("GET", extra={"url": url})
        response = self._session.get(url, timeout=self._timeout)
        response.raise_for_status()
        return response

    def get_text(self, url: str) -> str:
        response = self.get(url)
        # Sites brasileiros às vezes não declaram o charset; apparent_encoding adivinha.
        if response.encoding is None or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding
        return response.text

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        """Retorna (conteúdo, content-type) — útil para baixar imagens e PDFs."""
        response = self.get(url)
        return response.content, response.headers.get("Content-Type", "")

    def close(self) -> None:
        self._session.close()
