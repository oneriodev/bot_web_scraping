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


def test_importar_caixa_vazia(settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["importar", "assai"]) == 0
    assert "assai: 0 arquivo(s)" in capsys.readouterr().out
    assert (settings.inbox_dir / "assai").is_dir()  # a pasta é criada para você usar


def test_fonte_desconhecida_e_recusada() -> None:
    with pytest.raises(SystemExit):
        cli.main(["coletar", "concorrente_inexistente"])
