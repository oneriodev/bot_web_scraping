"""Acesso ao banco DuckDB (padrão Repository).

Nenhuma outra parte do projeto escreve SQL de gravação: elas chamam métodos daqui,
como `save_collections()` ou `pending_collections()`. Se um dia o banco virar
PostgreSQL, só este módulo (e o schema.sql) precisam mudar.

Uso:
    with Repository(settings.database_path, settings.data_dir) as repo:
        result = repo.save_collections(files)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Self

import duckdb

from monitor_precos.collectors.base import CollectedFile

if TYPE_CHECKING:  # só para os type hints; não carrega o Pydantic ao importar o módulo
    from monitor_precos.catalog import InternalProduct
    from monitor_precos.extractors.schemas import ExtractedOffer

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

EXTRACTION_STATUSES = ("pendente", "extraido", "revisao", "erro")
RUN_STATUSES = ("sucesso", "parcial", "falha")

# Colunas listadas explicitamente (nunca "SELECT *"): se alguém adicionar uma coluna
# na tabela, o código continua lendo exatamente o que espera, na ordem certa.
_COLLECTION_COLUMNS = (
    "id, concorrente, origem_url, caminho_arquivo, tipo_conteudo, sha256, "
    "campanha, validade_inicio, validade_fim, coletado_em, status_extracao"
)


@dataclass(frozen=True, slots=True)
class StoredCollection:
    """Uma linha da tabela `coletas`, já com o caminho absoluto do arquivo."""

    id: int
    competitor: str
    source_url: str
    local_path: Path
    content_type: str
    sha256: str
    campaign: str | None
    valid_from: date | None
    valid_until: date | None
    collected_at: datetime
    status: str


@dataclass(slots=True)
class SaveResult:
    """Resultado de uma gravação: o que entrou e o que já existia no banco."""

    inserted: list[CollectedFile] = field(default_factory=list)
    # (arquivo recém-baixado, caminho onde o MESMO conteúdo já estava guardado)
    duplicates: list[tuple[CollectedFile, Path]] = field(default_factory=list)
    # Conteúdo que o banco conhecia, mas cujo arquivo tinha sumido do disco:
    # o registro passou a apontar para a cópia nova.
    repaired: list[CollectedFile] = field(default_factory=list)


class Repository:
    """Conexão com o DuckDB + operações do pipeline."""

    def __init__(self, database_path: Path, data_dir: Path) -> None:
        self.database_path = database_path
        self.data_dir = data_dir
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(database_path))
        self.init_schema()

    # ------------------------------------------------------------ context manager
    # Permite usar `with Repository(...) as repo:` — a conexão é fechada ao sair do
    # bloco, mesmo se acontecer um erro no meio.
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    # ------------------------------------------------------------ esquema
    def init_schema(self) -> None:
        """Cria tabelas e sequências que ainda não existem (idempotente)."""
        self.connection.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ caminhos
    def _to_stored_path(self, path: Path) -> str:
        """Guarda o caminho RELATIVO à pasta data/: o banco continua válido se o
        projeto mudar de pasta ou de computador."""
        try:
            return path.resolve().relative_to(self.data_dir.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())

    def _from_stored_path(self, stored: str) -> Path:
        path = Path(stored)
        return path if path.is_absolute() else self.data_dir / path

    # ------------------------------------------------------------ coletas
    def save_collections(self, files: list[CollectedFile]) -> SaveResult:
        """Grava os arquivos coletados. Conteúdo já conhecido (mesmo sha256) é ignorado,
        a menos que o arquivo guardado tenha sumido do disco: aí o registro é reparado.

        Tudo acontece numa TRANSAÇÃO: ou grava todos, ou nenhum (se der erro no meio).
        """
        result = SaveResult()
        with self.connection.cursor() as cursor:
            cursor.begin()
            try:
                for file in files:
                    existing = cursor.execute(
                        "SELECT caminho_arquivo FROM coletas WHERE sha256 = ?", [file.sha256]
                    ).fetchone()
                    if existing:
                        stored = self._from_stored_path(existing[0])
                        if stored.exists():
                            result.duplicates.append((file, stored))
                            continue
                        # Banco e disco dessincronizados: o banco conhecia o conteúdo, mas
                        # o arquivo sumiu. Aponta para a cópia nova e, se a extração tinha
                        # dado erro por causa disso, devolve a coleta para a fila.
                        cursor.execute(
                            """
                            UPDATE coletas
                            SET caminho_arquivo = ?,
                                status_extracao = CASE WHEN status_extracao = 'erro'
                                                       THEN 'pendente'
                                                       ELSE status_extracao END
                            WHERE sha256 = ?
                            """,
                            [self._to_stored_path(file.local_path), file.sha256],
                        )
                        result.repaired.append(file)
                        continue

                    # Parâmetros "?" — NUNCA monte SQL concatenando strings (SQL injection).
                    cursor.execute(
                        """
                        INSERT INTO coletas (
                            concorrente, origem_url, caminho_arquivo, tipo_conteudo, sha256,
                            campanha, validade_inicio, validade_fim, coletado_em
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            file.competitor,
                            file.source_url,
                            self._to_stored_path(file.local_path),
                            str(file.content_type),
                            file.sha256,
                            file.campaign,
                            file.valid_from,
                            file.valid_until,
                            file.collected_at.replace(tzinfo=None),  # coluna guarda UTC "puro"
                        ],
                    )
                    result.inserted.append(file)
                cursor.commit()
            except Exception:
                cursor.rollback()
                raise

        logger.info(
            "Coletas gravadas no banco",
            extra={
                "inserted": len(result.inserted),
                "duplicates": len(result.duplicates),
                "repaired": len(result.repaired),
            },
        )
        return result

    def pending_collections(self, competitor: str | None = None) -> list[StoredCollection]:
        """Fila da extração: coletas com status 'pendente', da mais antiga para a mais nova."""
        sql = f"SELECT {_COLLECTION_COLUMNS} FROM coletas WHERE status_extracao = 'pendente'"
        params: list[object] = []
        if competitor:
            sql += " AND concorrente = ?"
            params.append(competitor)
        sql += " ORDER BY coletado_em, id"
        return [
            self._row_to_collection(row) for row in self.connection.execute(sql, params).fetchall()
        ]

    def collections_to_extract(
        self,
        competitor: str | None = None,
        *,
        include_errors: bool = False,
        limit: int | None = None,
    ) -> list[StoredCollection]:
        """Fila da extração com LLM: só imagens por enquanto (PDF entra na Etapa 4.2)."""
        statuses = ["pendente", "erro"] if include_errors else ["pendente"]
        placeholders = ", ".join("?" for _ in statuses)
        sql = (
            f"SELECT {_COLLECTION_COLUMNS} FROM coletas "
            f"WHERE tipo_conteudo IN ('image', 'pdf') AND status_extracao IN ({placeholders})"
        )
        params: list[object] = list(statuses)
        if competitor:
            sql += " AND concorrente = ?"
            params.append(competitor)
        sql += " ORDER BY coletado_em, id"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"  # int() garante que só entra um número no SQL
        return [
            self._row_to_collection(row) for row in self.connection.execute(sql, params).fetchall()
        ]

    def set_extraction_status(self, collection_id: int, status: str) -> None:
        if status not in EXTRACTION_STATUSES:
            raise ValueError(f"Status inválido: {status!r}. Use um de {EXTRACTION_STATUSES}.")
        self.connection.execute(
            "UPDATE coletas SET status_extracao = ? WHERE id = ?", [status, collection_id]
        )

    def _row_to_collection(self, row: tuple) -> StoredCollection:
        (id_, competitor, url, path, ctype, sha, campaign, vfrom, vuntil, at, status) = row
        return StoredCollection(
            id=id_,
            competitor=competitor,
            source_url=url,
            local_path=self._from_stored_path(path),
            content_type=ctype,
            sha256=sha,
            campaign=campaign,
            valid_from=vfrom,
            valid_until=vuntil,
            collected_at=at,
            status=status,
        )

    # ------------------------------------------------------------ ofertas
    def save_offers(
        self,
        collection_id: int,
        offers: list[ExtractedOffer],
        *,
        engine: str,
        collection_status: str,
    ) -> None:
        """Grava as ofertas de uma coleta e atualiza o status dela, numa única transação.

        Apaga ofertas antigas da mesma coleta antes: reprocessar não duplica nada.
        """
        if collection_status not in EXTRACTION_STATUSES:
            raise ValueError(f"Status inválido: {collection_status!r}.")
        review_status = "pendente" if collection_status == "revisao" else "automatico"
        now = _utc_now()

        with self.connection.cursor() as cursor:
            cursor.begin()
            try:
                cursor.execute("DELETE FROM ofertas_extraidas WHERE coleta_id = ?", [collection_id])
                cursor.execute(
                    "UPDATE coletas SET status_extracao = ? WHERE id = ?",
                    [collection_status, collection_id],
                )
                for offer in offers:
                    cursor.execute(
                        """
                        INSERT INTO ofertas_extraidas (
                            coleta_id, produto, marca, quantidade, unidade, preco_regular,
                            preco_promocional, condicao_especial, validade_inicio, validade_fim,
                            confianca, motor, extraido_em, status_revisao
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            collection_id,
                            offer.product,
                            offer.brand,
                            offer.quantity,
                            offer.unit,
                            offer.regular_price,
                            offer.promo_price,
                            offer.special_condition,
                            offer.valid_from,
                            offer.valid_until,
                            round(offer.confidence, 3),
                            engine,
                            now,
                            review_status,
                        ],
                    )
                cursor.commit()
            except Exception:
                cursor.rollback()
                raise

    # ------------------------------------------------------------ execuções
    def start_run(self, competitor: str) -> int:
        """Registra o início de uma execução e devolve o id dela.

        Fica gravado ANTES de coletar: se o processo travar no meio, sobra uma linha
        "em_andamento" sem finalizado_em — e isso também é uma informação útil.
        """
        row = self.connection.execute(
            "INSERT INTO execucoes (concorrente, iniciado_em) VALUES (?, ?) RETURNING id",
            [competitor, _utc_now()],
        ).fetchone()
        assert row is not None
        return int(row[0])

    def finish_run(
        self,
        run_id: int,
        status: str,
        *,
        inserted: int = 0,
        duplicates: int = 0,
        failures: int = 0,
        message: str | None = None,
    ) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(f"Status inválido: {status!r}. Use um de {RUN_STATUSES}.")
        self.connection.execute(
            """
            UPDATE execucoes
            SET finalizado_em = ?, status = ?, arquivos_novos = ?,
                arquivos_repetidos = ?, falhas = ?, mensagem = ?
            WHERE id = ?
            """,
            [_utc_now(), status, inserted, duplicates, failures, message, run_id],
        )

    def known_source_urls(self, competitor: str) -> set[str]:
        """URLs já coletadas CUJO ARQUIVO AINDA EXISTE no disco (para não baixar de novo).

        Se o arquivo foi apagado, a URL fica de fora e o coletor baixa outra vez.
        """
        rows = self.connection.execute(
            "SELECT origem_url, caminho_arquivo FROM coletas WHERE concorrente = ?", [competitor]
        ).fetchall()
        return {url for url, path in rows if self._from_stored_path(path).exists()}

        # ------------------------------------------------------------ produtos internos

    def upsert_products(self, products: list[InternalProduct]) -> int:
        """Grava o cadastro e recalcula a curva ABC, tudo numa transação.

        1. Os produtos vão para uma tabela TEMPORÁRIA (staging).
        2. Um único INSERT ... SELECT calcula a curva ABC com função de janela e grava.
        3. Produto que já existe é ATUALIZADO (upsert): reimportar não duplica nada.
        """
        if not products:
            return 0
        rows = [
            (p.id, p.barcode, p.description, p.brand, p.category,
             p.quantity, p.unit, p.sale_price, p.monthly_sales)
            for p in products
        ]  # fmt: skip

        with self.connection.cursor() as cursor:
            cursor.begin()
            try:
                cursor.execute(
                    """
                    CREATE OR REPLACE TEMP TABLE staging_produtos (
                        id INTEGER, codigo_barras VARCHAR, descricao VARCHAR, marca VARCHAR,
                        categoria VARCHAR, quantidade DECIMAL(10, 3), unidade VARCHAR,
                        preco_venda DECIMAL(10, 2), vendas_mes INTEGER
                    )
                    """
                )
                cursor.executemany(
                    "INSERT INTO staging_produtos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
                )
                cursor.execute(_UPSERT_PRODUCTS_SQL, [_utc_now()])
                cursor.execute("DROP TABLE staging_produtos")
                cursor.commit()
            except Exception:
                cursor.rollback()
                raise

        logger.info("Cadastro de produtos atualizado", extra={"products": len(rows)})
        return len(rows)

    def latest_valid_until(self, competitor: str) -> date | None:
        """A validade mais distante já anunciada pelo concorrente (None se nunca houve)."""
        row = self.connection.execute(
            "SELECT MAX(validade_fim) FROM coletas WHERE concorrente = ?", [competitor]
        ).fetchone()
        return row[0] if row else None


def _utc_now() -> datetime:
    """Agora em UTC, sem fuso anexado (é assim que as colunas TIMESTAMP guardam)."""
    return datetime.now(UTC).replace(tzinfo=None)


# Curva ABC (Pareto) com FUNÇÃO DE JANELA:
#   faturamento = preço × vendas do mês
#   fatia_antes = quanto do faturamento total já foi somado pelos produtos que vendem MAIS
#                 que este (soma acumulada, sem incluir ele mesmo)
#   A: começa antes de 80% | B: antes de 95% | C: o resto
_UPSERT_PRODUCTS_SQL = """
INSERT INTO produtos_internos (
    id, codigo_barras, descricao, marca, categoria, quantidade, unidade,
    curva_abc, preco_venda, atualizado_em
)
SELECT
    id, codigo_barras, descricao, marca, categoria, quantidade, unidade,
    CASE
        WHEN COALESCE(fatia_antes, 0) < 0.80 THEN 'A'
        WHEN fatia_antes < 0.95 THEN 'B'
        ELSE 'C'
    END AS curva_abc,
    preco_venda,
    ? AS atualizado_em
FROM (
    SELECT
        *,
        SUM(preco_venda * vendas_mes) OVER (
            ORDER BY preco_venda * vendas_mes DESC, id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) / NULLIF(SUM(preco_venda * vendas_mes) OVER (), 0) AS fatia_antes
    FROM staging_produtos
) AS ranking
ON CONFLICT (id) DO UPDATE SET
    codigo_barras = EXCLUDED.codigo_barras,
    descricao     = EXCLUDED.descricao,
    marca         = EXCLUDED.marca,
    categoria     = EXCLUDED.categoria,
    quantidade    = EXCLUDED.quantidade,
    unidade       = EXCLUDED.unidade,
    curva_abc     = EXCLUDED.curva_abc,
    preco_venda   = EXCLUDED.preco_venda,
    atualizado_em = EXCLUDED.atualizado_em
"""
