from datetime import date

import pytest

from monitor_precos.collectors.upload import (
    UploadedFile,
    content_matches_extension,
    import_uploads,
    slugify,
)
from monitor_precos.storage.repository import Repository

PNG = b"\x89PNG\r\n\x1a\n" + b"encarte-1"
PDF = b"%PDF-1.7\n" + b"encarte-2"


@pytest.fixture
def repo(settings):
    with Repository(settings.database_path, settings.data_dir) as repository:
        yield repository


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Assaí", "assai"),
        ("Fort Atacadista", "fort_atacadista"),
        ("  ATACADÃO  ", "atacadao"),
        ("Big-Lar!", "big_lar"),
    ],
)
def test_slugify(texto, esperado) -> None:
    assert slugify(texto) == esperado


def test_assinatura_do_arquivo() -> None:
    assert content_matches_extension(".png", PNG)
    assert content_matches_extension(".pdf", PDF)
    assert not content_matches_extension(".pdf", PNG)  # PNG renomeado para .pdf
    assert content_matches_extension(".webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ")


def test_importa_com_validade_e_nao_duplica(settings, repo) -> None:
    validade = {"valid_from": date(2026, 10, 15), "valid_until": date(2026, 10, 21)}

    primeiro = import_uploads(
        settings, repo, "assai", [UploadedFile("encarte.png", PNG)], campaign="semanal", **validade
    )
    assert primeiro.inserted == ["encarte.png"]

    # O mesmo encarte reenviado com outro nome: reconhecido pelo hash.
    segundo = import_uploads(settings, repo, "assai", [UploadedFile("copia.png", PNG)])
    assert segundo.inserted == []
    assert segundo.duplicates == ["copia.png"]

    linha = repo.connection.execute(
        "SELECT concorrente, campanha, validade_fim, tipo_conteudo FROM coletas"
    ).fetchall()
    assert linha == [("assai", "semanal", date(2026, 10, 21), "image")]


def test_recusa_formato_e_conteudo_falso(settings, repo) -> None:
    report = import_uploads(
        settings,
        repo,
        "assai",
        [
            UploadedFile("planilha.xlsx", b"PK..."),
            UploadedFile("falso.pdf", b"nao sou pdf"),
            UploadedFile("bom.pdf", PDF),
        ],
    )
    assert report.inserted == ["bom.pdf"]
    assert len(report.rejected) == 2

    status, falhas = repo.connection.execute("SELECT status, falhas FROM execucoes").fetchone()
    assert (status, falhas) == ("parcial", 2)


def test_slug_invalido_e_recusado(settings, repo) -> None:
    with pytest.raises(ValueError):
        import_uploads(settings, repo, "Assaí Atacadista", [UploadedFile("a.png", PNG)])
