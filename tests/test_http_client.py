"""Testes do cliente HTTP sem acessar a internet: usamos dublês (fakes)."""

import pytest
import requests

from monitor_precos.collectors.http_client import (
    PoliteHttpClient,
    RateLimiter,
    RobotsDisallowedError,
    RobotsPolicy,
)

ROBOTS_SERVE_MAIS = """
User-agent: *
Disallow: /wp-admin/
Allow: /wp-admin/admin-ajax.php
Allow: /wp-content/uploads/
"""


# ------------------------------------------------------------------ RateLimiter
class FakeClock:
    """Relógio de mentira: o tempo só anda quando o teste ou o sleep mandam."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_espera_entre_requisicoes_ao_mesmo_dominio() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock.time, sleep=clock.sleep)

    limiter.wait("site.com")  # 1ª requisição: não espera
    clock.now += 0.5  # meio segundo depois...
    limiter.wait("site.com")  # ...precisa esperar o 1,5 s restante

    assert clock.sleeps == [1.5]


def test_rate_limiter_nao_espera_entre_dominios_diferentes() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock.time, sleep=clock.sleep)

    limiter.wait("a.com")
    limiter.wait("b.com")

    assert clock.sleeps == []


# ------------------------------------------------------------------ RobotsPolicy
def test_robots_libera_uploads_e_bloqueia_admin() -> None:
    policy = RobotsPolicy("MonitorPrecosBot", lambda url: (200, ROBOTS_SERVE_MAIS))

    assert policy.can_fetch("https://redeservemais.com.br/wp-content/uploads/2026/09/x.webp")
    assert policy.can_fetch("https://redeservemais.com.br/ofertas_2/")
    assert not policy.can_fetch("https://redeservemais.com.br/wp-admin/")


def test_robots_inexistente_libera_tudo() -> None:
    policy = RobotsPolicy("MonitorPrecosBot", lambda url: (404, ""))
    assert policy.can_fetch("https://exemplo.com/qualquer")


@pytest.mark.parametrize("status", [401, 403])
def test_robots_proibido_bloqueia_tudo(status: int) -> None:
    policy = RobotsPolicy("MonitorPrecosBot", lambda url: (status, "Forbidden"))
    assert not policy.can_fetch("https://exemplo.com/qualquer")


def test_robots_e_baixado_uma_vez_por_dominio() -> None:
    chamadas: list[str] = []

    def fetch(url: str) -> tuple[int, str]:
        chamadas.append(url)
        return 200, ROBOTS_SERVE_MAIS

    policy = RobotsPolicy("MonitorPrecosBot", fetch)
    policy.can_fetch("https://site.com/a")
    policy.can_fetch("https://site.com/b")

    assert chamadas == ["https://site.com/robots.txt"]


# ------------------------------------------------------------------ PoliteHttpClient
class FakeSession(requests.Session):
    """Sessão que responde a partir de um dicionário {url: (status, corpo)}."""

    def __init__(self, routes: dict[str, tuple[int, str]]) -> None:
        super().__init__()
        self.routes = routes
        self.requested: list[str] = []

    def get(self, url, **kwargs):  # type: ignore[override]
        self.requested.append(url)
        status, body = self.routes.get(url, (404, ""))
        response = requests.Response()
        response.status_code = status
        response._content = body.encode()
        response.url = url
        response.encoding = "utf-8"
        return response


def _client(settings, routes):
    session = FakeSession(routes)
    limiter = RateLimiter(0, sleep=lambda s: None)
    return PoliteHttpClient(settings, session=session, rate_limiter=limiter), session


def test_cliente_envia_user_agent_identificado(settings) -> None:
    client, session = _client(settings, {})
    assert session.headers["User-Agent"] == "MonitorPrecosBot/test"


def test_cliente_recusa_url_bloqueada_sem_requisitar(settings) -> None:
    client, session = _client(
        settings, {"https://site.com/robots.txt": (200, "User-agent: *\nDisallow: /")}
    )

    with pytest.raises(RobotsDisallowedError):
        client.get_text("https://site.com/ofertas")

    assert session.requested == ["https://site.com/robots.txt"]  # a página nem foi pedida


def test_cliente_baixa_pagina_permitida(settings) -> None:
    client, _ = _client(
        settings,
        {
            "https://site.com/robots.txt": (200, ROBOTS_SERVE_MAIS),
            "https://site.com/ofertas": (200, "<html>ok</html>"),
        },
    )
    assert client.get_text("https://site.com/ofertas") == "<html>ok</html>"


def test_cliente_levanta_erro_http(settings) -> None:
    client, _ = _client(settings, {"https://site.com/robots.txt": (404, "")})
    with pytest.raises(requests.HTTPError):
        client.get_text("https://site.com/sumiu")
