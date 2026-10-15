import shutil
from decimal import Decimal

import pytest
from PIL import Image, ImageDraw

from monitor_precos.extractors.ocr import (
    OCR_MAX_CONFIDENCE,
    TesseractExtractor,
    group_lines,
    parse_ocr_lines,
)
from monitor_precos.extractors.schemas import ExtractedOffer


def test_parse_de_por_e_embalagem() -> None:
    linhas = ["PAPEL TOALHA", "FELITA HOME 2UN", "DE R$ 17,99", "POR 15,99"]
    [item] = parse_ocr_lines(linhas, confidence=0.6)
    oferta = ExtractedOffer.model_validate(item)

    assert oferta.promo_price == Decimal("15.99")  # menor preço = "por"
    assert oferta.regular_price == Decimal("17.99")  # maior preço = "de"
    assert (oferta.quantity, oferta.unit) == (Decimal("2"), "un")
    assert oferta.product == "PAPEL TOALHA FELITA HOME"


def test_peso_nao_vira_preco() -> None:
    [item] = parse_ocr_lines(["CARNE MOIDA", "1,50 kg", "R$ 29,90"], confidence=0.6)
    assert item["promo_price"] == "29.90"
    assert item["regular_price"] is None


def test_sem_preco_nao_gera_oferta() -> None:
    assert parse_ocr_lines(["LOGO SERVE MAIS"], confidence=0.6) == []


def test_group_lines_ignora_vazios_e_junta_por_linha() -> None:
    data = {
        "text": ["", "ARROZ", "TIPO", "1", "27,90"],
        "conf": ["-1", "91", "88", "75", "95"],
        "block_num": [1, 1, 1, 1, 1],
        "par_num": [1, 1, 1, 1, 1],
        "line_num": [0, 1, 1, 1, 2],
    }
    linhas, confiancas = group_lines(data)
    assert linhas == ["ARROZ TIPO 1", "27,90"]
    assert confiancas == [91.0, 88.0, 75.0, 95.0]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract não instalado")
def test_extrator_real_nunca_passa_do_teto_de_confianca() -> None:
    imagem = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(imagem).text((10, 40), "ARROZ TIPO 1  R$ 27,90", fill="black")
    import io

    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    saida = TesseractExtractor().extract(buffer.getvalue(), "image/png")

    assert all(item["confidence"] <= OCR_MAX_CONFIDENCE for item in saida.items)
