from PIL import Image

from monitor_precos.extractors.crop import crop_box, offer_image
from monitor_precos.extractors.schemas import ExtractedOffer


def _oferta(box):
    return ExtractedOffer.model_validate(
        {"product": "Arroz Branco", "promo_price": "22,50", "confidence": 0.9, "box_2d": box}
    )


def test_recorta_com_margem() -> None:
    pagina = Image.new("RGB", (1000, 2000), "white")
    card = crop_box(pagina, (0, 0, 500, 500))  # canto superior esquerdo, metade × metade
    assert card.size == (520, 1040)  # + 2% de margem em cada lado (sem passar da borda)


def test_sem_caixa_mostra_a_pagina_inteira(tmp_path) -> None:
    arquivo = tmp_path / "encarte.png"
    Image.new("RGB", (300, 400)).save(arquivo)
    assert offer_image(arquivo).size == (300, 400)
    assert offer_image(arquivo, 0, (0, 0, 500, 1000)).size == (156, 400)


def test_caixa_valida_e_ajustada() -> None:
    assert _oferta([100, 0, 250, 166]).box_2d == (100, 0, 250, 166)
    assert _oferta([-5, 0, 1200, 500]).box_2d == (0, 0, 1000, 500)  # presa entre 0 e 1000


def test_caixa_invalida_nao_derruba_a_oferta() -> None:
    assert _oferta([300, 0, 100, 500]).box_2d is None  # ymin maior que ymax
    assert _oferta([1, 2, 3]).box_2d is None  # só 3 números
    assert _oferta(None).box_2d is None
    assert _oferta([300, 0, 100, 500]).product == "Arroz Branco"  # a oferta continua
