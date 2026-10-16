"""Painel de decisão: nosso preço × concorrentes (Streamlit).

Rodar:
    uv run streamlit run src/monitor_precos/dashboard/painel_app.py
"""

from __future__ import annotations

from dataclasses import astuple
from datetime import datetime

import duckdb
import pandas as pd
import streamlit as st

from monitor_precos.analytics.comparison import (
    FilterOptions,
    Filters,
    comparison,
    filter_options,
    for_export,
    price_history,
    to_csv_bytes,
    to_excel_bytes,
)
from monitor_precos.config import get_settings
from monitor_precos.matching.service import refresh_offer_keys
from monitor_precos.storage.repository import Repository

settings = get_settings()
today = datetime.now(settings.tz).date()  # "hoje" no fuso da operação (Cuiabá)

MONEY = st.column_config.NumberColumn(format="R$ %.2f")
DAY = st.column_config.DateColumn(format="DD/MM/YYYY")
COLUMN_CONFIG = {
    "Nosso preço": MONEY,
    "Preço do concorrente": MONEY,
    "Preço equivalente": MONEY,
    "Diferença (R$)": MONEY,
    "Diferença (%)": st.column_config.NumberColumn(format="%.1f%%"),
    "Válido de": DAY,
    "Válido até": DAY,
    "Coletado em": DAY,
    "Confiança": st.column_config.NumberColumn(format="%.2f"),
}


# ------------------------------------------------------------------ acesso ao banco
def read_only() -> duckdb.DuckDBPyConnection:
    """Conexão SOMENTE LEITURA: o painel nunca altera dados por acidente."""
    return duckdb.connect(str(settings.database_path), read_only=True)


@st.cache_data(ttl=60, show_spinner=False)
def load_options() -> FilterOptions:
    with Repository(settings.database_path, settings.data_dir) as repo:
        refresh_offer_keys(repo)  # única escrita do painel: chaves das ofertas novas
    with read_only() as connection:
        return filter_options(connection)


# O cache do Streamlit precisa de argumentos simples: os filtros viajam como tupla.
@st.cache_data(ttl=60, show_spinner=False)
def load_comparison(filters_tuple: tuple) -> pd.DataFrame:
    with read_only() as connection:
        return comparison(connection, Filters(*filters_tuple))


@st.cache_data(ttl=60, show_spinner=False)
def load_history(product_id: int, filters_tuple: tuple) -> pd.DataFrame:
    with read_only() as connection:
        return price_history(connection, product_id, Filters(*filters_tuple))


def situation(difference_pct: float) -> str:
    if difference_pct > 0:
        return "🔴 mais caro"
    if difference_pct < 0:
        return "🟢 mais barato"
    return "⚪ igual"


def show_table(data: pd.DataFrame) -> None:
    table = for_export(data)
    table.insert(0, "Situação", data["diferenca_pct"].map(situation).to_numpy())
    st.dataframe(table, hide_index=True, column_config=COLUMN_CONFIG)


# ------------------------------------------------------------------ carga inicial
st.title("📊 Painel de preços: nós × concorrentes")

if not settings.database_path.exists():
    st.warning("Banco ainda não existe. Rode uma coleta primeiro.")
    st.stop()

try:
    options = load_options()
except duckdb.IOException:
    st.error("Banco em uso por outro processo (coleta/extração?). Aguarde e recarregue a página.")
    st.stop()

if not options.competitors:
    st.info(
        "Nenhuma oferta vinculada ainda. Aprove vínculos na tela de de-para: "
        "`uv run streamlit run src/monitor_precos/dashboard/de_para_app.py`"
    )
    st.stop()

# ------------------------------------------------------------------ filtros
st.sidebar.header("Filtros")
competitors = st.sidebar.multiselect("Concorrentes", options.competitors, placeholder="Todos")
categories = st.sidebar.multiselect("Categorias", options.categories, placeholder="Todas")
period = st.sidebar.date_input(
    "Período da coleta", value=(options.first_date, options.last_date), format="DD/MM/YYYY"
)
# Enquanto o usuário escolhe, o date_input devolve só a 1ª data: repete-a como fim.
start, end = (*period, *period)[:2] if period else (None, None)
validity = st.sidebar.radio("Validade da oferta", ["Todas", "Somente vigentes hoje"])
include_low = st.sidebar.checkbox(
    "Incluir extrações de baixa confiança",
    value=True,
    help="Ofertas que a extração mandou para revisão (confiança abaixo do mínimo).",
)
if st.sidebar.button("🔄 Recarregar dados"):
    st.cache_data.clear()
    st.rerun()

filters = Filters(
    competitors=tuple(competitors),
    categories=tuple(categories),
    collected_from=start,
    collected_until=end,
    valid_on=today if validity == "Somente vigentes hoje" else None,
    include_low_confidence=include_low,
)
data = load_comparison(astuple(filters))

if data.empty:
    st.info("Nenhuma oferta com esses filtros. Tente 'Todas' na validade ou amplie o período.")
    st.stop()

# ------------------------------------------------------------------ métricas
more_expensive = data[data["diferenca_pct"] > 0]
curve_a = more_expensive[more_expensive["curva_abc"] == "A"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Comparações (produto × concorrente)", len(data))
col2.metric(
    "Estamos mais caros em", f"{len(more_expensive)} ({len(more_expensive) / len(data):.0%})"
)
col3.metric("Curva A mais caros", len(curve_a))
col4.metric("Diferença média", f"{data['diferenca_pct'].mean():+.1f}%")

# ------------------------------------------------------------------ destaque Curva A
st.subheader("🚨 Curva A: onde estamos mais caros")
st.caption("Os itens que mais pesam no faturamento: centavos aqui levam a compra do mês embora.")
if curve_a.empty:
    st.success("Nenhum item da Curva A mais caro que o concorrente com esses filtros.")
else:
    show_table(curve_a.sort_values("diferenca_pct", ascending=False))

tab_all, tab_history, tab_export = st.tabs(["Comparativo completo", "Histórico", "Exportar"])

with tab_all:
    st.caption(
        "Oferta mais recente de cada concorrente por produto. 'Tamanho ajustado' = preço "
        "do concorrente convertido para o tamanho da nossa embalagem."
    )
    show_table(data)

with tab_history:
    products = data.drop_duplicates("produto_id").sort_values("nosso_produto")
    labels = {
        int(r.produto_id): f"{r.produto_id} · {r.nosso_produto}" for r in products.itertuples()
    }
    chosen = st.selectbox("Produto", list(labels), format_func=labels.get)
    history = load_history(chosen, astuple(filters))

    chart = history.pivot_table(
        index="data_referencia", columns="concorrente", values="preco_concorrente", aggfunc="min"
    )
    chart["nosso preço (atual)"] = history["nosso_preco"].iloc[0]
    st.line_chart(chart)
    st.caption(
        "Data = início da validade da oferta (ou o dia da coleta). Nosso preço é uma linha "
        "reta porque o cadastro guarda só o preço ATUAL. Com um único encarte por "
        "concorrente o gráfico ainda tem só um ponto: o histórico cresce a cada coleta."
    )
    st.dataframe(history, hide_index=True, column_config={"data_referencia": DAY})

with tab_export:
    st.write(f"{len(data)} linha(s) com os filtros atuais.")
    st.warning("A planilha contém preços internos: não envie para fora da empresa nem ao GitHub.")
    stamp = today.strftime("%Y%m%d")
    st.download_button(
        "⬇ Excel (.xlsx)",
        to_excel_bytes(data),
        file_name=f"comparativo_precos_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.download_button(
        "⬇ CSV (separado por ;)",
        to_csv_bytes(data),
        file_name=f"comparativo_precos_{stamp}.csv",
        mime="text/csv",
    )
