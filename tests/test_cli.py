from datetime import date

import duckdb
import pytest

from monitor_precos import cli
from monitor_precos.collectors.base import BaseCollector


@pytest.fixture(autouse=True)
def _settings_temporario(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    """Faz o CLI usar as pastas temporárias dos testes, nunca o data/ real."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "setup_logging", lambda *args: None)


def test_fontes_lista_serve_mais(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["fontes"]) == 0
    assert "serve_mais" in capsys.readouterr().out


def test_importar_grava_no_banco_e_nao_duplica(
    settings, capsys: pytest.CaptureFixture[str]
) -> None:
    inbox = settings.inbox_dir / "assai"
    inbox.mkdir(parents=True)
    (inbox / "encarte.jpg").write_bytes(b"foto")

    assert cli.main(["importar", "assai"]) == 0
    assert "1 arquivo(s) novo(s), 0 já conhecido(s)" in capsys.readouterr().out

    # O mesmo encarte chega de novo (reenviado no WhatsApp com outro nome):
    (inbox / "encarte_reenviado.jpg").write_bytes(b"foto")
    assert cli.main(["importar", "assai"]) == 0
    assert "0 arquivo(s) novo(s), 1 já conhecido(s)" in capsys.readouterr().out
    assert settings.database_path.exists()


def test_resumo_e_sql(settings, capsys: pytest.CaptureFixture[str]) -> None:
    (settings.inbox_dir / "assai").mkdir(parents=True)
    (settings.inbox_dir / "assai" / "encarte.jpg").write_bytes(b"foto")
    cli.main(["importar", "assai"])
    capsys.readouterr()

    assert cli.main(["resumo"]) == 0
    assert "assai" in capsys.readouterr().out

    assert cli.main(["sql", "SELECT COUNT(*) AS total FROM coletas"]) == 0
    assert "total" in capsys.readouterr().out


def test_sql_sem_banco_e_sql_invalido(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sql", "SELECT 1"]) == 1  # banco ainda não existe

    cli.main(["importar", "assai"])  # cria o banco
    assert cli.main(["sql", "SELEC errado"]) == 1
    assert "Erro no SQL" in capsys.readouterr().err


def test_sql_e_somente_leitura(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["importar", "assai"])
    assert cli.main(["sql", "DELETE FROM coletas"]) == 1


def test_fonte_desconhecida_e_recusada() -> None:
    with pytest.raises(SystemExit):
        cli.main(["coletar", "concorrente_inexistente"])


# ------------------------------------------------------------------ Etapa 3: execuções
def test_run_status() -> None:
    assert cli.run_status(inserted=5, duplicates=0, failures=0) == "sucesso"
    assert cli.run_status(inserted=0, duplicates=5, failures=0) == "sucesso"
    assert cli.run_status(inserted=4, duplicates=0, failures=1) == "parcial"
    assert cli.run_status(inserted=0, duplicates=0, failures=3) == "falha"


def test_staleness_warning() -> None:
    hoje = date(2026, 10, 11)
    assert cli.staleness_warning("serve_mais", None, hoje) is None  # nunca teve validade
    assert cli.staleness_warning("serve_mais", date(2026, 10, 11), hoje) is None  # vale hoje
    aviso = cli.staleness_warning("serve_mais", date(2026, 10, 2), hoje)
    assert aviso is not None and "há 9 dia(s)" in aviso


class _ColetorQueQuebra(BaseCollector):
    competitor_slug = "quebrado"
    competitor_name = "Site fora do ar"

    def __init__(self, settings, http) -> None:
        super().__init__(settings)

    def collect(self):
        raise ConnectionError("site fora do ar")


def test_falha_e_registrada_e_retorna_codigo_1(
    monkeypatch: pytest.MonkeyPatch, settings, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(cli.WEB_COLLECTORS, "quebrado", _ColetorQueQuebra)

    assert cli.main(["coletar", "quebrado"]) == 1
    assert "FALHA" in capsys.readouterr().err

    with duckdb.connect(str(settings.database_path), read_only=True) as conn:
        status, mensagem, fim = conn.execute(
            "SELECT status, mensagem, finalizado_em FROM execucoes"
        ).fetchone()
    assert status == "falha"
    assert "site fora do ar" in mensagem
    assert fim is not None


def test_importacao_registra_execucao_com_sucesso(settings) -> None:
    (settings.inbox_dir / "assai").mkdir(parents=True)
    (settings.inbox_dir / "assai" / "encarte.jpg").write_bytes(b"foto")

    assert cli.main(["importar", "assai"]) == 0
    assert cli.main(["execucoes"]) == 0

    with duckdb.connect(str(settings.database_path), read_only=True) as conn:
        assert conn.execute("SELECT status, arquivos_novos FROM execucoes").fetchone() == (
            "sucesso",
            1,
        )
