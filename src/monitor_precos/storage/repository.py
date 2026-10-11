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
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import duckdb

from monitor_precos.collectors.base import CollectedFile

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

EXTRACTION_STATUSES = ("pendente", "extraido", "revisao", "erro")

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
        """Grava os arquivos coletados. Conteúdo já conhecido (mesmo sha256) é ignorado.

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
                        result.duplicates.append((file, self._from_stored_path(existing[0])))
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
            extra={"inserted": len(result.inserted), "duplicates": len(result.duplicates)},
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
