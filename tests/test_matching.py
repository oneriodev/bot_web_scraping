from decimal import Decimal
from pathlib import Path

import pytest

from monitor_precos.catalog import InternalProduct
from monitor_precos.matching.matcher import CatalogItem, group_offers, size_similarity, suggest
from monitor_precos.storage.repository import Repository

CADASTRO = [
    CatalogItem(1017, "Fermento Químico em Pó", "Royal", "Mercearia", Decimal("100"), "g",
                Decimal("4.59"), "C"),
    CatalogItem(1020, "Leite Condensado", "Moça", "Mercearia", Decimal("395"), "g",
                Decimal("7.99"), "B"),
    CatalogItem(1024, "Leite UHT Integral", "Piracanjuba", "Laticínios", Decimal("1"), "l",
                Decimal("5.29"), "A"),
]  # fmt: skip


def test_marca_propria_do_concorrente_casa_com_o_equivalente() -> None:
    [melhor, *_] = suggest("Fermento Químico em Pó", "Felitá Chef", Decimal("100"), "g", CADASTRO)
    assert melhor.product.id == 1017
    assert melhor.score >= 90  # nome 100, tamanho 100, marca diferente (neutra)


def test_tamanho_desempata_produtos_de_nome_parecido() -> None:
    [melhor, *_] = suggest("Leite Integral", None, Decimal("1"), "l", CADASTRO)
    assert melhor.product.id == 1024  # 1 l casa com 1 l; leite condensado é em gramas


@pytest.mark.parametrize(
    ("a", "b", "esperado"),
    [
        ((Decimal("100"), "g"), (Decimal("0.1"), "kg"), 100.0),  # mesmo tamanho
        ((Decimal("1"), "l"), (Decimal("395"), "g"), 0.0),  # ml × g
        ((None, None), (Decimal("1"), "kg"), 50.0),  # desconhecido: neutro
        ((Decimal("500"), "g"), (Decimal("1"), "kg"), 35.0),  # metade do tamanho
    ],
)
def test_size_similarity(a, b, esperado) -> None:
    assert size_similarity(*a, *b) == pytest.approx(esperado)


def test_group_offers_junta_a_mesma_chave() -> None:
    linhas = [
        (1, "serve_mais", "Fermento Químico em Pó", "Felitá Chef", Decimal("100"), "g",
         Decimal("3.79"), Path("a.webp")),
        (2, "serve_mais", "FERMENTO QUIMICO EM PO", "Felita Chef", Decimal("100"), "g",
         Decimal("3.79"), Path("b.webp")),
    ]  # fmt: skip
    [grupo] = group_offers(linhas)
    assert grupo.offer_ids == (1, 2)


# ------------------------------------------------------------------ banco
@pytest.fixture
def repo(settings):
    with Repository(settings.database_path, settings.data_dir) as repository:
        repository.upsert_products(
            [InternalProduct.model_validate(
                {"id": 1017, "descricao": "Fermento Químico em Pó", "categoria": "Mercearia",
                 "preco_venda": "4,59", "vendas_mes": 10}
            )]
        )  # fmt: skip
        yield repository


def test_aprovar_rejeitar_e_desfazer(repo) -> None:
    chave = "fermento quimico po felita chef 100g"
    repo.approve_match("serve_mais", chave, 1017, 95.0)
    repo.approve_match("serve_mais", chave, 1017, 90.0)  # reaprovar não duplica
    repo.reject_match("serve_mais", "cerveja skol 269ml")

    assert repo.decided_keys() == {("serve_mais", chave), ("serve_mais", "cerveja skol 269ml")}
    assert len(repo.approved_matches()) == 1

    repo.undo_decision("serve_mais", "cerveja skol 269ml")
    assert ("serve_mais", "cerveja skol 269ml") not in repo.decided_keys()
