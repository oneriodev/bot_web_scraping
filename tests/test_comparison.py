from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from monitor_precos.analytics.comparison import (
    Filters,
    comparison,
    price_history,
    to_csv_bytes,
)
from monitor_precos.catalog import InternalProduct
from monitor_precos.collectors.base import CollectedFile, ContentType
from monitor_precos.extractors.schemas import ExtractedOffer
from monitor_precos.matching.normalize import offer_key
from monitor_precos.matching.service import refresh_offer_keys
from monitor_precos.storage.repository import Repository


def _oferta(produto: str, marca: str, quantidade: str, unidade: str, preco: str):
    return ExtractedOffer.model_validate(
        {
            "product": produto,
            "brand": marca,
            "quantity": quantidade,
            "unit": unidade,
            "regular_price": None,
            "promo_price": preco,
            "special_condition": None,
            "valid_from": "23/09/2026",
            "valid_until": "02/10/2026",
            "confidence": 0.95,
        }
    )


@pytest.fixture
def repo(settings):
    with Repository(settings.database_path, settings.data_dir) as repository:
        repository.upsert_products(
            [
                InternalProduct.model_validate(
                    {"id": 1017, "descricao": "Fermento Químico em Pó", "marca": "Royal",
                     "categoria": "Mercearia", "quantidade": "100", "unidade": "g",
                     "preco_venda": "4,59", "vendas_mes": 10}
                ),
                InternalProduct.model_validate(
                    {"id": 1024, "descricao": "Leite UHT Integral", "marca": "Piracanjuba",
                     "categoria": "Laticínios", "quantidade": "1", "unidade": "l",
                     "preco_venda": "5,29", "vendas_mes": 100}
                ),
            ]
        )  # fmt: skip

        imagem = settings.raw_dir / "serve_mais" / "2026-10-11" / "card.webp"
        imagem.parent.mkdir(parents=True)
        imagem.write_bytes(b"card")
        repository.save_collections(
            [
                CollectedFile(
                    competitor="serve_mais",
                    source_url="https://site/card.webp",
                    local_path=imagem,
                    content_type=ContentType.IMAGE,
                    sha256="h1",
                    collected_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
                )
            ]
        )
        [coleta] = repository.pending_collections()
        repository.save_offers(
            coleta.id,
            [
                _oferta("Fermento Químico em Pó", "Felitá Chef", "100", "g", "3,79"),
                _oferta("Leite UHT Integral", "Italac", "500", "ml", "2,50"),
            ],
            engine="llm:teste",
            collection_status="extraido",
        )

        refresh_offer_keys(repository)
        chave_fermento = offer_key("Fermento Químico em Pó", "Felitá Chef", Decimal("100"), "g")
        chave_leite = offer_key("Leite UHT Integral", "Italac", Decimal("500"), "ml")
        repository.approve_match("serve_mais", chave_fermento, 1017, 95.0)
        repository.approve_match("serve_mais", chave_leite, 1024, 80.0)
        yield repository


def test_compara_pelo_preco_equivalente(repo) -> None:
    df = comparison(repo.connection, Filters()).set_index("produto_id")

    fermento = df.loc[1017]
    assert not fermento.tamanho_ajustado  # 100 g × 100 g: compara direto
    assert fermento.preco_conc_equivalente == pytest.approx(3.79)
    assert fermento.diferenca_pct == pytest.approx(21.1)  # (4,59 - 3,79) / 3,79

    leite = df.loc[1024]
    assert leite.tamanho_ajustado  # 500 ml × 1 l
    assert leite.preco_conc_equivalente == pytest.approx(5.00)  # 2,50 × 1000/500
    assert leite.diferenca_pct == pytest.approx(5.8)
    assert leite.curva_abc == "A"


def test_maior_diferenca_vem_primeiro(repo) -> None:
    df = comparison(repo.connection, Filters())
    assert df["produto_id"].tolist() == [1017, 1024]


def test_filtro_de_vigencia(repo) -> None:
    assert len(comparison(repo.connection, Filters(valid_on=date(2026, 10, 1)))) == 2
    assert comparison(repo.connection, Filters(valid_on=date(2026, 10, 15))).empty  # vencidas


def test_filtros_de_categoria_e_concorrente(repo) -> None:
    df = comparison(repo.connection, Filters(categories=("Laticínios",)))
    assert df["produto_id"].tolist() == [1024]
    assert comparison(repo.connection, Filters(competitors=("assai",))).empty


def test_historico_e_csv(repo) -> None:
    historico = price_history(repo.connection, 1024, Filters())
    assert historico["preco_concorrente"].tolist() == [5.0]

    csv = to_csv_bytes(comparison(repo.connection, Filters())).decode("utf-8-sig")
    assert csv.splitlines()[0].startswith("Concorrente;Código;Nosso produto")
    assert "5,29" in csv  # vírgula decimal, padrão do Excel brasileiro
