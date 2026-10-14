from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from monitor_precos.extractors.schemas import ExtractedOffer, parse_brl


def _oferta(**alteracoes):
    base = {
        "product": "Arroz Branco Tipo 1",
        "brand": "Tio João",
        "quantity": 5,
        "unit": "kg",
        "regular_price": None,
        "promo_price": 27.9,
        "special_condition": None,
        "valid_from": None,
        "valid_until": None,
        "confidence": 0.9,
    }
    return {**base, **alteracoes}


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("R$ 12,99", Decimal("12.99")),
        ("1.299,90", Decimal("1299.90")),
        (12.99, Decimal("12.99")),
        ("12.99", Decimal("12.99")),
        (None, None),
        ("", None),
    ],
)
def test_parse_brl(entrada, esperado) -> None:
    assert parse_brl(entrada) == esperado


@pytest.mark.parametrize(
    ("bruto", "unidade"),
    [("KG", "kg"), ("Kg.", "kg"), ("LT", "l"), ("ML", "ml"), ("gr", "g"), ("UND", "un"), ("caixa", None)],
)  # fmt: skip
def test_unidade_padronizada(bruto: str, unidade: str | None) -> None:
    assert ExtractedOffer.model_validate(_oferta(unit=bruto)).unit == unidade


def test_preco_de_menor_que_por_e_descartado_e_reduz_confianca() -> None:
    oferta = ExtractedOffer.model_validate(_oferta(regular_price=20, promo_price=25))
    assert oferta.regular_price is None
    assert oferta.confidence == 0.5


def test_data_brasileira_e_aceita() -> None:
    oferta = ExtractedOffer.model_validate(_oferta(valid_until="02/10/2026"))
    assert oferta.valid_until == date(2026, 10, 2)


def test_data_impossivel_vira_none() -> None:
    oferta = ExtractedOffer.model_validate(_oferta(valid_until="31/02/2026"))
    assert oferta.valid_until is None


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("promo_price", None), ("promo_price", 0), ("product", ""), ("confidence", 1.5)],
)
def test_item_invalido_e_recusado(campo: str, valor) -> None:
    with pytest.raises(ValidationError):
        ExtractedOffer.model_validate(_oferta(**{campo: valor}))
