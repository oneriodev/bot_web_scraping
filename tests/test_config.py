from pathlib import Path

import pytest

from monitor_precos.config import PROJECT_ROOT, load_settings


def test_valores_padrao(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("DATA_DIR", "USER_AGENT", "REQUEST_DELAY_SECONDS"):
        monkeypatch.delenv(var, raising=False)

    settings = load_settings(env_file=tmp_path / "nao_existe.env")

    assert settings.data_dir == PROJECT_ROOT / "data"
    assert settings.raw_dir == PROJECT_ROOT / "data" / "raw"
    assert "MonitorPrecosBot" in settings.user_agent  # o bot se identifica
    assert settings.request_delay_seconds >= 1  # nunca martelar o site


def test_variaveis_de_ambiente_sobrescrevem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "outra"))
    monkeypatch.setenv("REQUEST_DELAY_SECONDS", "5")

    settings = load_settings(env_file=tmp_path / "nao_existe.env")

    assert settings.data_dir == tmp_path / "outra"
    assert settings.request_delay_seconds == 5.0


def test_importar_nao_cria_pastas(settings) -> None:
    assert not settings.data_dir.exists()
    settings.ensure_directories()
    assert settings.inbox_dir.is_dir()
