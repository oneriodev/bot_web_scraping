import hashlib
from datetime import UTC, datetime

import pytest

from monitor_precos.collectors.base import (
    BaseCollector,
    CollectedFile,
    ContentType,
    safe_filename,
)


class DummyCollector(BaseCollector):
    """Coletor mínimo, só para testar o que a classe base oferece."""

    competitor_slug = "dummy"
    competitor_name = "Concorrente de Teste"

    def collect(self) -> list[CollectedFile]:
        return [
            self.save_file(
                b"conteudo-fake",
                "encarte 1.webp",
                "https://dummy.com/encarte.webp",
                ContentType.IMAGE,
                collected_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
            )
        ]


def test_nao_instancia_classe_abstrata(settings) -> None:
    with pytest.raises(TypeError):
        BaseCollector(settings)  # type: ignore[abstract]


def test_save_file_grava_em_pasta_por_data_e_calcula_hash(settings) -> None:
    [arquivo] = DummyCollector(settings).collect()

    assert arquivo.local_path == settings.raw_dir / "dummy" / "2026-10-11" / "encarte_1.webp"
    assert arquivo.local_path.read_bytes() == b"conteudo-fake"
    assert arquivo.sha256 == hashlib.sha256(b"conteudo-fake").hexdigest()
    assert arquivo.competitor == "dummy"
    assert arquivo.content_type is ContentType.IMAGE


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("encarte 1.webp", "encarte_1.webp"),
        ("../../etc/passwd", "etc_passwd"),  # impede sair da pasta de destino
        ("Ofertão Açougue.jpg", "Ofertao_Acougue.jpg"),
        ("", "arquivo"),
    ],
)
def test_safe_filename(entrada: str, esperado: str) -> None:
    assert safe_filename(entrada) == esperado
