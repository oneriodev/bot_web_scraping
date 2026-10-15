"""Sugestão de vínculos entre produtos do concorrente e o nosso cadastro (rapidfuzz).

Pontuação de 0 a 100 = 65% nome + 25% tamanho + 10% marca.
A marca pesa pouco de propósito: boa parte dos encartes é marca PRÓPRIA do
concorrente, que nunca bate com a nossa — o que importa é o produto equivalente.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from rapidfuzz import fuzz

from monitor_precos.matching.normalize import base_size, normalize_text, offer_key, strip_size

NAME_WEIGHT = 0.65
SIZE_WEIGHT = 0.25
BRAND_WEIGHT = 0.10


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Um produto do NOSSO cadastro (mesma ordem de colunas de repo.catalog_items())."""

    id: int
    description: str
    brand: str | None
    category: str
    quantity: Decimal | None
    unit: str | None
    sale_price: Decimal
    abc_curve: str


@dataclass(frozen=True, slots=True)
class Candidate:
    product: CatalogItem
    score: float  # nota final 0-100
    name_score: float
    size_score: float
    same_brand: bool


@dataclass(frozen=True, slots=True)
class OfferToMatch:
    """Um produto do concorrente aguardando vínculo (ofertas iguais agrupadas pela chave)."""

    competitor: str
    key: str
    product: str
    brand: str | None
    quantity: Decimal | None
    unit: str | None
    promo_price: Decimal
    image_path: Path
    offer_ids: tuple[int, ...]


def name_similarity(a: str, b: str) -> float:
    """Média de token_set (tolera palavras extras) e token_sort (pune palavras faltando).

    Só token_set daria 100 para "leite" × "leite condensado" — perigoso.
    """
    return (fuzz.token_set_ratio(a, b) + fuzz.token_sort_ratio(a, b)) / 2


def size_similarity(
    quantity_a: Decimal | None, unit_a: str | None, quantity_b: Decimal | None, unit_b: str | None
) -> float:
    """100 = mesmo tamanho | até 70 = mesma medida, tamanho diferente | 0 = g × ml."""
    a, b = base_size(quantity_a, unit_a), base_size(quantity_b, unit_b)
    if a is None or b is None:
        return 50.0  # tamanho desconhecido: neutro, nem ajuda nem atrapalha
    if a[1] != b[1]:
        return 0.0  # gramas × mililitros: produtos diferentes
    if a[0] == b[0]:
        return 100.0
    return float(min(a[0], b[0]) / max(a[0], b[0])) * 70


def suggest(
    product: str,
    brand: str | None,
    quantity: Decimal | None,
    unit: str | None,
    catalog: Iterable[CatalogItem],
    limit: int = 5,
) -> list[Candidate]:
    """Os `limit` produtos do cadastro mais parecidos, do mais para o menos provável."""
    name = strip_size(normalize_text(product))
    normalized_brand = normalize_text(brand)

    candidates: list[Candidate] = []
    for item in catalog:
        item_name = strip_size(normalize_text(item.description))
        name_score = name_similarity(name, item_name)
        size_score = size_similarity(quantity, unit, item.quantity, item.unit)
        same_brand = bool(normalized_brand) and normalized_brand == normalize_text(item.brand)
        score = (
            NAME_WEIGHT * name_score
            + SIZE_WEIGHT * size_score
            + BRAND_WEIGHT * (100 if same_brand else 50)  # marca diferente: neutro
        )
        candidates.append(
            Candidate(item, round(score, 1), round(name_score, 1), round(size_score, 1), same_brand)
        )
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)[:limit]


def group_offers(rows: Iterable[tuple]) -> list[OfferToMatch]:
    """Agrupa ofertas com a mesma chave: o mesmo produto em 3 encartes = 1 decisão só.

    `rows` vem de repo.offers_for_matching():
        (oferta_id, concorrente, produto, marca, quantidade, unidade, preço, caminho_imagem)
    """
    groups: dict[tuple[str, str], OfferToMatch] = {}
    for offer_id, competitor, product, brand, quantity, unit, price, image in rows:
        key = offer_key(product, brand, quantity, unit)
        existing = groups.get((competitor, key))
        if existing is None:
            groups[(competitor, key)] = OfferToMatch(
                competitor, key, product, brand, quantity, unit, price, image, (offer_id,)
            )
        else:
            groups[(competitor, key)] = replace(existing, offer_ids=(*existing.offer_ids, offer_id))
    return list(groups.values())
