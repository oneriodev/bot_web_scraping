"""Orquestração do de-para: liga o matcher (funções puras) ao banco."""

from __future__ import annotations

from monitor_precos.matching.matcher import OfferToMatch, group_offers
from monitor_precos.storage.repository import Repository


def refresh_offer_keys(repo: Repository) -> list[OfferToMatch]:
    """Recalcula a chave normalizada de todas as ofertas e grava em chaves_ofertas.

    Precisa rodar antes de qualquer leitura que dependa do de-para: ofertas
    re-extraídas ganham ids novos e ainda não têm chave.
    """
    groups = group_offers(repo.offers_for_matching())
    repo.sync_offer_keys(
        [
            (offer_id, group.competitor, group.key)
            for group in groups
            for offer_id in group.offer_ids
        ]
    )
    return groups
