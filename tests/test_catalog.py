from decimal import Decimal

import pytest

from monitor_precos.catalog import EXAMPLE_PRODUCTS_CSV, InternalProduct, load_products_csv
from monitor_precos.storage.repository import Repository


@pytest.fixture
def repo(settings):
    with Repository(settings.database_path, settings.data_dir) as repository:
        yield repository


def _produto(id_: int, preco: str, vendas: int) -> InternalProduct:
    return InternalProduct.model_validate(
        {"id": id_, "descricao": f"Produto {id_}", "categoria": "Mercearia",
         "preco_venda": preco, "vendas_mes": vendas}
    )  # fmt: skip


def test_csv_de_exemplo_e_valido() -> None:
    """O próprio arquivo versionado no repositório precisa passar na validação."""
    produtos, erros = load_products_csv(EXAMPLE_PRODUCTS_CSV)
    assert erros == []
    assert len(produtos) >= 40


def test_linha_com_erro_e_apontada(tmp_path) -> None:
    caminho = tmp_path / "produtos.csv"
    caminho.write_text(
        "id;codigo_barras;descricao;marca;categoria;quantidade;unidade;preco_venda;vendas_mes\n"
        "1;;Arroz Tipo 1;Camil;Mercearia;5;KG;26,49;100\n"
        "2;;Feijão;Camil;Mercearia;1;kg;0;50\n"  # preço zero: inválido
        "1;;Repetido;X;Mercearia;1;kg;1,00;1\n",  # id repetido
        encoding="utf-8",
    )
    produtos, erros = load_products_csv(caminho)

    assert [p.id for p in produtos] == [1]
    assert produtos[0].unit == "kg"  # "KG" padronizado
    assert len(erros) == 2
    assert erros[0].startswith("linha 3: preco_venda")
    assert "repetido" in erros[1]


def test_curva_abc_por_faturamento(repo) -> None:
    # Faturamentos: 1000, 150 e 50 (total 1200).
    # Produto 1 começa em 0% → A | produto 2 começa em 83% → B | produto 3 começa em 96% → C
    repo.upsert_products(
        [_produto(1, "10,00", 100), _produto(2, "1,50", 100), _produto(3, "0,50", 100)]
    )

    curvas = dict(repo.connection.execute("SELECT id, curva_abc FROM produtos_internos").fetchall())
    assert curvas == {1: "A", 2: "B", 3: "C"}


def test_reimportar_atualiza_sem_duplicar(repo) -> None:
    repo.upsert_products([_produto(1, "10,00", 100)])
    repo.upsert_products([_produto(1, "12,00", 100)])  # preço mudou

    total, preco = repo.connection.execute(
        "SELECT COUNT(*), MAX(preco_venda) FROM produtos_internos"
    ).fetchone()
    assert (total, preco) == (1, Decimal("12.00"))
