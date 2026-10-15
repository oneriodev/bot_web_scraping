from decimal import Decimal

import pytest

from monitor_precos.matching.normalize import format_size, normalize_text, offer_key, strip_size


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Fermento Químico em Pó", "fermento quimico po"),
        ("Absorvente c/ Abas", "absorvente abas"),
        ("Óleo de Soja 900ML", "oleo soja 900 ml"),
        ("Água Mineral 1,5L", "agua mineral 1.5 l"),
        ("Queijo Mussarela Fatiado", "queijo mucarela fatiado"),
        (None, ""),
    ],
)
def test_normalize_text(entrada, esperado) -> None:
    assert normalize_text(entrada) == esperado


def test_strip_size() -> None:
    assert strip_size("oleo soja 900 ml") == "oleo soja"
    assert strip_size("papel toalha 2 un 200 fls") == "papel toalha"


@pytest.mark.parametrize(
    ("quantidade", "unidade", "esperado"),
    [
        (Decimal("5"), "kg", "5000g"),
        (Decimal("1.5"), "l", "1500ml"),
        (Decimal("100.000"), "g", "100g"),
        (Decimal("2.000"), "un", "2un"),
        (None, None, ""),
    ],
)
def test_format_size(quantidade, unidade, esperado) -> None:
    assert format_size(quantidade, unidade) == esperado


def test_offer_key_estavel_entre_semanas() -> None:
    semana_1 = offer_key("Fermento Químico em Pó", "Felitá Chef", Decimal("100"), "g")
    semana_2 = offer_key("FERMENTO QUIMICO EM PO 100G", "Felita Chef", Decimal("0.1"), "kg")
    assert semana_1 == semana_2 == "fermento quimico po felita chef 100g"
