"""Fixtures compartilhadas pelos testes."""

from pathlib import Path

import pytest

from monitor_precos.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings apontando para uma pasta temporária: os testes nunca tocam em data/ real."""
    return Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        log_level="DEBUG",
        user_agent="MonitorPrecosBot/test",
        request_timeout_seconds=5,
        request_delay_seconds=2.0,
        max_retries=0,
    )
