"""Comparativo de preços: nosso preço × concorrente (Etapa 7).

Só LEITURA: as funções recebem uma conexão DuckDB (pode ser somente leitura) e
devolvem DataFrames do pandas, prontos para o painel e para a exportação.

Caminho dos dados:
    ofertas_extraidas → chaves_ofertas → de_para_concorrente → produtos_internos
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

import duckdb
import pandas as pd

# Consulta-base em etapas (CTEs). {where} recebe os filtros montados em _where().
_BASE_SQL = """
WITH vinculadas AS (
    SELECT
        o.id                                            AS oferta_id,
        c.concorrente,
        CAST(c.coletado_em AS DATE)                     AS data_coleta,
        COALESCE(o.validade_inicio, c.validade_inicio)  AS validade_inicio,
        COALESCE(o.validade_fim, c.validade_fim)        AS validade_fim,
        o.produto                                       AS produto_concorrente,
        o.marca                                         AS marca_concorrente,
        o.condicao_especial,
        CAST(o.confianca AS DOUBLE)                     AS confianca,
        o.status_revisao,
        CAST(o.preco_promocional AS DOUBLE)             AS preco_concorrente,
        p.id                                            AS produto_id,
        p.descricao                                     AS nosso_produto,
        p.marca                                         AS nossa_marca,
        p.categoria,
        p.curva_abc,
        CAST(p.preco_venda AS DOUBLE)                   AS nosso_preco,
        -- Tamanhos na unidade-base (kg → g, l → ml) para comparar embalagens diferentes.
        CAST(o.quantidade AS DOUBLE)
            * CASE WHEN o.unidade IN ('kg', 'l') THEN 1000 ELSE 1 END       AS tam_conc,
        CASE o.unidade WHEN 'kg' THEN 'g' WHEN 'l' THEN 'ml' ELSE o.unidade END AS base_conc,
        CAST(p.quantidade AS DOUBLE)
            * CASE WHEN p.unidade IN ('kg', 'l') THEN 1000 ELSE 1 END       AS tam_nosso,
        CASE p.unidade WHEN 'kg' THEN 'g' WHEN 'l' THEN 'ml' ELSE p.unidade END AS base_nosso
    FROM ofertas_extraidas o
    JOIN coletas c             ON c.id = o.coleta_id
    JOIN chaves_ofertas k      ON k.oferta_id = o.id
    JOIN de_para_concorrente d ON d.concorrente = k.concorrente
                              AND d.descricao_concorrente = k.chave
    JOIN produtos_internos p   ON p.id = d.produto_id
    WHERE o.status_revisao <> 'rejeitado'
),
com_tamanho AS (
    SELECT
        *,
        -- Ajusta só quando os dois tamanhos são conhecidos, da mesma medida e diferentes.
        COALESCE(
            tam_conc > 0 AND tam_nosso > 0 AND base_conc = base_nosso AND tam_conc <> tam_nosso,
            FALSE
        ) AS tamanho_ajustado
    FROM vinculadas
),
filtradas AS (
    SELECT
        *,
        CASE WHEN tamanho_ajustado
             THEN ROUND(preco_concorrente * tam_nosso / tam_conc, 2)  -- proporcional
             ELSE preco_concorrente
        END AS preco_conc_equivalente
    FROM com_tamanho
    WHERE {where}
)
SELECT
    *,
    ROUND(nosso_preco - preco_conc_equivalente, 2)                                 AS diferenca_rs,
    ROUND((nosso_preco - preco_conc_equivalente) / preco_conc_equivalente * 100, 1) AS diferenca_pct
FROM filtradas
"""

# Nome técnico → cabeçalho para a equipe de compras (tela e planilha).
EXPORT_COLUMNS = {
    "concorrente": "Concorrente",
    "produto_id": "Código",
    "nosso_produto": "Nosso produto",
    "categoria": "Categoria",
    "curva_abc": "Curva",
    "nosso_preco": "Nosso preço",
    "produto_concorrente": "Produto do concorrente",
    "marca_concorrente": "Marca do concorrente",
    "preco_concorrente": "Preço do concorrente",
    "preco_conc_equivalente": "Preço equivalente",
    "tamanho_ajustado": "Tamanho ajustado",
    "diferenca_rs": "Diferença (R$)",
    "diferenca_pct": "Diferença (%)",
    "condicao_especial": "Condição",
    "validade_inicio": "Válido de",
    "validade_fim": "Válido até",
    "data_coleta": "Coletado em",
    "confianca": "Confiança",
}


@dataclass(frozen=True)
class Filters:
    """Filtros do painel. Tupla vazia / None = sem filtro."""

    competitors: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    collected_from: date | None = None
    collected_until: date | None = None
    valid_on: date | None = None  # só ofertas vigentes neste dia
    include_low_confidence: bool = True  # extrações que caíram na fila de revisão


@dataclass(frozen=True)
class FilterOptions:
    """Valores disponíveis para os filtros (o que existe no banco)."""

    competitors: list[str]
    categories: list[str]
    first_date: date | None
    last_date: date | None


def _where(filters: Filters) -> tuple[str, list[object]]:
    """Monta o WHERE com trechos FIXOS + parâmetros "?" (nada do usuário vira SQL)."""
    conditions = ["TRUE"]
    params: list[object] = []
    if filters.competitors:
        conditions.append("list_contains(?, concorrente)")
        params.append(list(filters.competitors))
    if filters.categories:
        conditions.append("list_contains(?, categoria)")
        params.append(list(filters.categories))
    if filters.collected_from:
        conditions.append("data_coleta >= ?")
        params.append(filters.collected_from)
    if filters.collected_until:
        conditions.append("data_coleta <= ?")
        params.append(filters.collected_until)
    if filters.valid_on:
        # Validade desconhecida NÃO conta como vigente: decisão de preço pede certeza.
        conditions.append("validade_fim >= ? AND COALESCE(validade_inicio, validade_fim) <= ?")
        params += [filters.valid_on, filters.valid_on]
    if not filters.include_low_confidence:
        conditions.append("status_revisao <> 'pendente'")
    return " AND ".join(conditions), params


def _base(filters: Filters) -> tuple[str, list[object]]:
    where, params = _where(filters)
    return _BASE_SQL.format(where=where), params


def comparison(connection: duckdb.DuckDBPyConnection, filters: Filters) -> pd.DataFrame:
    """A oferta MAIS RECENTE de cada par concorrente × produto, maior diferença primeiro."""
    base, params = _base(filters)
    sql = f"""
        SELECT * FROM ({base})
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY concorrente, produto_id
            ORDER BY data_coleta DESC, validade_fim DESC NULLS LAST, oferta_id DESC
        ) = 1
        ORDER BY diferenca_pct DESC
    """
    return connection.execute(sql, params).df()


def price_history(
    connection: duckdb.DuckDBPyConnection, product_id: int, filters: Filters
) -> pd.DataFrame:
    """Todas as ofertas de um produto nosso ao longo do tempo, por concorrente."""
    base, params = _base(filters)
    sql = f"""
        SELECT
            COALESCE(validade_inicio, data_coleta) AS data_referencia,
            concorrente,
            MIN(preco_conc_equivalente)            AS preco_concorrente,
            ANY_VALUE(nosso_preco)                 AS nosso_preco
        FROM ({base})
        WHERE produto_id = ?
        GROUP BY ALL
        ORDER BY data_referencia, concorrente
    """
    return connection.execute(sql, [*params, product_id]).df()


def filter_options(connection: duckdb.DuckDBPyConnection) -> FilterOptions:
    """Concorrentes, categorias e intervalo de datas que existem nas ofertas vinculadas."""
    base, params = _base(Filters())
    row = connection.execute(
        f"""
        SELECT list_sort(list_distinct(list(concorrente))),
               list_sort(list_distinct(list(categoria))),
               MIN(data_coleta), MAX(data_coleta)
        FROM ({base})
        """,
        params,
    ).fetchone()
    competitors, categories, first, last = row if row else (None, None, None, None)
    return FilterOptions(competitors or [], categories or [], first, last)


# ------------------------------------------------------------------ exportação
def for_export(data: pd.DataFrame) -> pd.DataFrame:
    """Só as colunas úteis para compras, com cabeçalhos em português."""
    return data[list(EXPORT_COLUMNS)].rename(columns=EXPORT_COLUMNS)


def to_csv_bytes(data: pd.DataFrame) -> bytes:
    """CSV no padrão do Excel brasileiro: ";" separa colunas, "," é o decimal."""
    return for_export(data).to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def to_excel_bytes(data: pd.DataFrame) -> bytes:
    """Planilha .xlsx em memória (nada é gravado no disco do servidor)."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for_export(data).to_excel(writer, index=False, sheet_name="Comparativo")
    return buffer.getvalue()
