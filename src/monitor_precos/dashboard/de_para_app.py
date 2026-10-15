"""Tela de aprovação do de-para (Streamlit).

Rodar:
    uv run streamlit run src/monitor_precos/dashboard/de_para_app.py

O Streamlit roda o script INTEIRO de novo a cada clique. Por isso o banco é aberto
e fechado em cada leitura/gravação (with Repository...): a conexão fica aberta só
por instantes e não trava os comandos de coleta/extração no terminal.
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from monitor_precos.config import get_settings
from monitor_precos.matching.matcher import CatalogItem, OfferToMatch, group_offers, suggest
from monitor_precos.matching.normalize import format_size
from monitor_precos.storage.repository import Repository

st.set_page_config(page_title="De-para · Monitor de Preços", page_icon="🔗", layout="wide")
settings = get_settings()


def money(value: Decimal | None) -> str:
    """Decimal → "R$ 1.299,90" (formato brasileiro)."""
    if value is None:
        return "—"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def describe(item: CatalogItem) -> str:
    size = format_size(item.quantity, item.unit) or "tamanho ?"
    return f"{item.description} · {item.brand or 'sem marca'} · {size}"


def load_state() -> tuple[list[OfferToMatch], list[CatalogItem], list[tuple]]:
    with Repository(settings.database_path, settings.data_dir) as repo:
        groups = group_offers(repo.offers_for_matching())
        # Guarda a chave de cada oferta: é a ponte que o painel (Etapa 7) usa no SQL.
        repo.sync_offer_keys([(oid, g.competitor, g.key) for g in groups for oid in g.offer_ids])
        decided = repo.decided_keys()
        catalog = [CatalogItem(*row) for row in repo.catalog_items()]
        approved = repo.approved_matches()
    pending = [g for g in groups if (g.competitor, g.key) not in decided]
    return pending, catalog, approved


pending, catalog, approved = load_state()

st.title("🔗 De-para: produto do concorrente → nosso produto")
if not catalog:
    st.warning("Cadastro interno vazio. Rode: `uv run monitor-precos importar-produtos`")
    st.stop()

# ------------------------------------------------------------------ barra lateral
competitors = sorted({group.competitor for group in pending})
chosen_competitor = st.sidebar.selectbox("Concorrente", ["Todos", *competitors])
if chosen_competitor != "Todos":
    pending = [group for group in pending if group.competitor == chosen_competitor]

st.sidebar.metric("Aguardando decisão", len(pending))
st.sidebar.metric("Vínculos aprovados", len(approved))

# "Pular" guarda a chave na sessão do navegador: ela vai para o fim da fila.
skipped: set[tuple[str, str]] = st.session_state.setdefault("skipped", set())
queue = [g for g in pending if (g.competitor, g.key) not in skipped]
if pending and not queue:  # pulou todos: recomeça a fila
    skipped.clear()
    queue = pending

tab_review, tab_approved = st.tabs(["Revisar", "Aprovados"])

# ------------------------------------------------------------------ aba Revisar
with tab_review:
    if not queue:
        st.success("Nada pendente. Todos os produtos do concorrente já foram decididos. 🎉")
    else:
        item = queue[0]
        col_image, col_decision = st.columns([1, 2])

        with col_image:
            if item.image_path.exists():
                st.image(str(item.image_path), width=320)
            else:
                st.info("Imagem não encontrada no disco.")

        with col_decision:
            st.subheader(item.product)
            st.caption(
                f"**{item.competitor}** · marca: {item.brand or '—'} · "
                f"tamanho: {format_size(item.quantity, item.unit) or '?'} · "
                f"preço: **{money(item.promo_price)}** · aparece em {len(item.offer_ids)} oferta(s)"
            )
            st.code(item.key, language=None)  # a chave normalizada que ficará salva

            candidates = suggest(item.product, item.brand, item.quantity, item.unit, catalog)
            labels = {
                f"{c.score:5.1f} pts — {describe(c.product)} · nosso preço "
                f"{money(c.product.sale_price)} · curva {c.product.abc_curve}": c
                for c in candidates
            }
            chosen_label = st.radio("Sugestões (maior pontuação primeiro)", list(labels), index=0)
            chosen = labels[chosen_label]
            st.caption(
                f"nome {chosen.name_score:.0f} · tamanho {chosen.size_score:.0f} · "
                f"marca {'igual' if chosen.same_brand else 'diferente'}"
            )

            with st.expander("Nenhuma sugestão serve? Buscar no cadastro inteiro"):
                manual = st.selectbox(
                    "Produto",
                    [None, *catalog],
                    format_func=lambda p: "—" if p is None else f"{p.id} · {describe(p)}",
                    key=f"manual-{item.competitor}-{item.key}",
                )

            col_ok, col_none, col_skip = st.columns(3)
            if col_ok.button("✅ Aprovar vínculo", type="primary"):
                target = manual or chosen.product
                score = chosen.score if target.id == chosen.product.id else None
                with Repository(settings.database_path, settings.data_dir) as repo:
                    repo.approve_match(item.competitor, item.key, target.id, score)
                st.toast(f"Vinculado a: {target.description}")
                st.rerun()
            if col_none.button("🚫 Sem equivalente"):
                with Repository(settings.database_path, settings.data_dir) as repo:
                    repo.reject_match(item.competitor, item.key)
                st.toast("Marcado como sem equivalente no nosso cadastro.")
                st.rerun()
            if col_skip.button("⏭ Pular"):
                skipped.add((item.competitor, item.key))
                st.rerun()

# ------------------------------------------------------------------ aba Aprovados
with tab_approved:
    if not approved:
        st.info("Nenhum vínculo aprovado ainda.")
    else:
        st.dataframe(
            [
                {
                    "concorrente": competitor,
                    "produto do concorrente (chave)": key,
                    "nosso produto": f"{product_id} · {description} · {brand or ''}",
                    "pontuação": score,
                    "aprovado em (UTC)": approved_at,
                }
                for competitor, key, product_id, description, brand, score, approved_at in approved
            ],
            hide_index=True,
        )
        to_undo = st.selectbox(
            "Desfazer um vínculo (a chave volta para a fila)",
            [None, *[(row[0], row[1]) for row in approved]],
            format_func=lambda pair: "—" if pair is None else f"{pair[0]} · {pair[1]}",
        )
        if to_undo and st.button("↩ Desfazer"):
            with Repository(settings.database_path, settings.data_dir) as repo:
                repo.undo_decision(*to_undo)
            st.rerun()
