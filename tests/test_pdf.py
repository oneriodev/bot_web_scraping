import pymupdf
import pytest

from monitor_precos.extractors.base import ExtractionOutput, merge_outputs
from monitor_precos.extractors.pdf import load_pages, pdf_to_png_pages


def _criar_pdf(path, paginas: int) -> None:
    documento = pymupdf.open()
    for numero in range(paginas):
        pagina = documento.new_page()
        pagina.insert_text((72, 72), f"Arroz 5kg R$ 27,90 - pagina {numero + 1}")
    documento.save(str(path))
    documento.close()


def test_pdf_vira_uma_imagem_png_por_pagina(tmp_path) -> None:
    pdf = tmp_path / "encarte.pdf"
    _criar_pdf(pdf, paginas=2)

    paginas = pdf_to_png_pages(pdf, dpi=72)

    assert len(paginas) == 2
    assert all(png.startswith(b"\x89PNG") for png in paginas)  # assinatura do formato PNG


def test_pdf_com_paginas_demais_e_recusado(tmp_path) -> None:
    pdf = tmp_path / "enorme.pdf"
    _criar_pdf(pdf, paginas=3)
    with pytest.raises(ValueError):
        pdf_to_png_pages(pdf, max_pages=2)


def test_load_pages_de_imagem(tmp_path) -> None:
    imagem = tmp_path / "card.webp"
    imagem.write_bytes(b"conteudo")
    assert load_pages(imagem, "image") == [(b"conteudo", "image/webp")]


def test_merge_outputs_junta_paginas() -> None:
    a = ExtractionOutput(True, [{"x": 1}], input_tokens=10, output_tokens=2)
    b = ExtractionOutput(False, [{"x": 2}], input_tokens=5, output_tokens=1)

    juntos = merge_outputs([a, b])

    assert juntos.is_offer_image is True
    assert juntos.items == [{"x": 1}, {"x": 2}]
    assert (juntos.input_tokens, juntos.output_tokens) == (15, 3)
