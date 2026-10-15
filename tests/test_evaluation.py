from decimal import Decimal

from monitor_precos.extractors.evaluation import (
    GroundTruthRow,
    OfferView,
    Score,
    load_ground_truth,
    score,
    write_template,
)

GABARITO = GroundTruthRow("a.webp", "Papel Toalha", Decimal("17.99"), Decimal("15.99"))


def test_score_com_acerto() -> None:
    resultado = score(
        [OfferView("Papel Toalha Folha Dupla", Decimal("17.99"), Decimal("15.99"))], GABARITO
    )
    assert resultado.promo_ok and resultado.regular_ok
    assert resultado.product_similarity > 0.6


def test_score_escolhe_a_oferta_com_o_preco_certo() -> None:
    ofertas = [
        OfferView("Outro produto", None, Decimal("9.99")),
        OfferView("Papel Toalha", Decimal("17.99"), Decimal("15.99")),
    ]
    assert score(ofertas, GABARITO).promo_ok


def test_score_sem_ofertas() -> None:
    assert score([], GABARITO) == Score(promo_ok=False, regular_ok=False, product_similarity=0.0)


def test_template_e_leitura_do_gabarito(tmp_path) -> None:
    caminho = tmp_path / "gabarito.csv"
    write_template(caminho, ["a.webp", "b.webp"])
    assert load_ground_truth(caminho) == []  # em branco: nada preenchido ainda

    caminho.write_text(
        "arquivo;produto;preco_regular;preco_promocional\n"
        "a.webp;Arroz Tipo 1;32,90;27,90\n"
        "b.webp;;;\n",
        encoding="utf-8",
    )
    [linha] = load_ground_truth(caminho)
    assert linha.promo_price == Decimal("27.90")
    assert linha.regular_price == Decimal("32.90")



def test_gabarito_salvo_com_virgula_pelo_libreoffice(tmp_path) -> None:
    caminho = tmp_path / "gabarito.csv"
    caminho.write_text(
        "arquivo,produto,preco_regular,preco_promocional\n"
        ",,,\n"
        'a.webp,papel toalha,"17,99","15,99"\n',
        encoding="utf-8",
    )
    [linha] = load_ground_truth(caminho)  # a linha ",,," vazia é ignorada
    assert linha.promo_price == Decimal("15.99")
    assert linha.regular_price == Decimal("17.99")