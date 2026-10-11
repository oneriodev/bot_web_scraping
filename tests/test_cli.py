import pytest

from monitor_precos import cli


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
