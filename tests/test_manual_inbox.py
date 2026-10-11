import pytest

from monitor_precos.collectors.base import ContentType
from monitor_precos.collectors.manual_inbox import ManualInboxCollector


def test_importa_imagens_e_pdfs_e_move_para_processados(settings) -> None:
    inbox = settings.inbox_dir / "assai"
    inbox.mkdir(parents=True)
    (inbox / "encarte.jpg").write_bytes(b"foto-whatsapp")
    (inbox / "folheto.PDF").write_bytes(b"%PDF-fake")
    (inbox / "anotacoes.txt").write_text("ignorar")

    files = ManualInboxCollector(settings, "assai").collect()

    assert sorted(f.content_type for f in files) == [ContentType.IMAGE, ContentType.PDF]
    assert all(f.competitor == "assai" for f in files)
    assert all(f.local_path.is_relative_to(settings.raw_dir / "assai") for f in files)
    # Originais saíram da caixa de entrada; o .txt não suportado ficou onde estava.
    assert (inbox / "processados" / "encarte.jpg").exists()
    assert not (inbox / "encarte.jpg").exists()
    assert (inbox / "anotacoes.txt").exists()


def test_rodar_de_novo_nao_reimporta(settings) -> None:
    inbox = settings.inbox_dir / "assai"
    inbox.mkdir(parents=True)
    (inbox / "encarte.jpg").write_bytes(b"foto")

    collector = ManualInboxCollector(settings, "assai")
    assert len(collector.collect()) == 1
    assert collector.collect() == []  # idempotente


@pytest.mark.parametrize("slug", ["Assaí", "../etc", "fort atacadista", ""])
def test_rejeita_nome_de_concorrente_invalido(settings, slug: str) -> None:
    with pytest.raises(ValueError):
        ManualInboxCollector(settings, slug)
