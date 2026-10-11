"""Linha de comando do projeto.

Exemplos:
    uv run monitor-precos fontes
    uv run monitor-precos coletar serve_mais
    uv run monitor-precos importar assai      # arquivos de data/inbox/assai/
    uv run monitor-precos resumo
    uv run monitor-precos sql "SELECT concorrente, COUNT(*) FROM coletas GROUP BY 1"
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable

import duckdb

from monitor_precos.collectors.base import BaseCollector
from monitor_precos.collectors.http_client import PoliteHttpClient
from monitor_precos.collectors.manual_inbox import ManualInboxCollector
from monitor_precos.collectors.serve_mais import ServeMaisCollector
from monitor_precos.config import Settings, get_settings
from monitor_precos.logging_config import setup_logging
from monitor_precos.storage.repository import Repository

logger = logging.getLogger("monitor_precos.cli")

# Registro de coletores de site: para adicionar um concorrente, crie o coletor e
# inclua uma linha aqui. Nada mais no projeto precisa mudar.
WEB_COLLECTORS: dict[str, Callable[[Settings, PoliteHttpClient], BaseCollector]] = {
    ServeMaisCollector.competitor_slug: ServeMaisCollector,
}

SUMMARY_SQL = """
SELECT
    concorrente,
    COALESCE(campanha, '(sem campanha)')            AS campanha,
    COALESCE(strftime(validade_fim, '%d/%m/%Y'), '?') AS valido_ate,
    status_extracao,
    COUNT(*)                                         AS arquivos,
    strftime(MAX(coletado_em), '%d/%m/%Y %H:%M')     AS ultima_coleta_utc
FROM coletas
GROUP BY ALL
ORDER BY concorrente, MAX(coletado_em) DESC
"""


def _run(collector: BaseCollector, repo: Repository) -> int:
    """Coleta, grava no banco e imprime o resumo. Devolve o código de saída (0 = sucesso)."""
    try:
        files = collector.collect()
        result = repo.save_collections(files)
    except Exception:
        # Erro inesperado: registra o traceback completo no log e sinaliza falha
        # com código 1 (o cron/agendador usa isso para saber que deu errado).
        logger.exception("Coleta falhou", extra={"competitor": collector.competitor_slug})
        return 1

    # Conteúdo que o banco já conhecia: apaga a cópia nova do disco (a antiga continua
    # guardada no caminho registrado). Cuidado para não apagar o próprio original!
    for file, stored_path in result.duplicates:
        if file.local_path.resolve() != stored_path.resolve():
            file.local_path.unlink(missing_ok=True)

    print(
        f"{collector.competitor_slug}: {len(result.inserted)} arquivo(s) novo(s), "
        f"{len(result.duplicates)} já conhecido(s)"
    )
    for file in result.inserted:
        validity = f"até {file.valid_until:%d/%m/%Y}" if file.valid_until else "validade ?"
        print(f"  {file.local_path.name:<55} {validity:<16} {file.sha256[:12]}")
    return 0


def _run_sql(settings: Settings, query: str) -> int:
    """Executa uma consulta SOMENTE LEITURA e imprime o resultado como tabela."""
    if not settings.database_path.exists():
        print("Banco ainda não existe. Rode uma coleta primeiro.", file=sys.stderr)
        return 1
    with duckdb.connect(str(settings.database_path), read_only=True) as connection:
        try:
            connection.sql(query).show(max_rows=200, max_width=200)
        except duckdb.Error as error:
            print(f"Erro no SQL: {error}", file=sys.stderr)
            return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor-precos", description="Monitor de preços de concorrentes."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fontes", help="lista os coletores de site disponíveis")

    collect = sub.add_parser("coletar", help="coleta encartes de um site concorrente")
    collect.add_argument("fonte", choices=sorted(WEB_COLLECTORS))

    inbox = sub.add_parser("importar", help="importa arquivos de data/inbox/<concorrente>/")
    inbox.add_argument("concorrente", help="ex.: assai, fort_atacadista")

    sub.add_parser("resumo", help="resumo das coletas gravadas no banco")

    sql = sub.add_parser("sql", help="executa uma consulta SQL (somente leitura)")
    sql.add_argument("consulta", help='ex.: "SELECT * FROM coletas LIMIT 5"')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    settings.ensure_directories()
    setup_logging(settings.log_dir, settings.log_level)

    if args.command == "fontes":
        for slug in sorted(WEB_COLLECTORS):
            print(slug)
        return 0
    if args.command == "sql":
        return _run_sql(settings, args.consulta)
    if args.command == "resumo":
        return _run_sql(settings, SUMMARY_SQL)

    with Repository(settings.database_path, settings.data_dir) as repo:
        if args.command == "importar":
            return _run(ManualInboxCollector(settings, args.concorrente), repo)

        http = PoliteHttpClient(settings)
        try:
            return _run(WEB_COLLECTORS[args.fonte](settings, http), repo)
        finally:
            http.close()


if __name__ == "__main__":
    sys.exit(main())
