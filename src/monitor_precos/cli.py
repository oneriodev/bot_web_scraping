"""Linha de comando do projeto.

Exemplos:
    uv run monitor-precos fontes
    uv run monitor-precos coletar serve_mais
    uv run monitor-precos importar assai      # arquivos de data/inbox/assai/
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable

from monitor_precos.collectors.base import BaseCollector
from monitor_precos.collectors.http_client import PoliteHttpClient
from monitor_precos.collectors.manual_inbox import ManualInboxCollector
from monitor_precos.collectors.serve_mais import ServeMaisCollector
from monitor_precos.config import Settings, get_settings
from monitor_precos.logging_config import setup_logging

logger = logging.getLogger("monitor_precos.cli")

# Registro de coletores de site: para adicionar um concorrente, crie o coletor e
# inclua uma linha aqui. Nada mais no projeto precisa mudar.
WEB_COLLECTORS: dict[str, Callable[[Settings, PoliteHttpClient], BaseCollector]] = {
    ServeMaisCollector.competitor_slug: ServeMaisCollector,
}


def _run(collector: BaseCollector) -> int:
    """Executa um coletor e imprime o resumo. Devolve o código de saída (0 = sucesso)."""
    try:
        files = collector.collect()
    except Exception:
        # Erro inesperado: registra o traceback completo no log e sinaliza falha
        # com código 1 (o cron/agendador usa isso para saber que deu errado).
        logger.exception("Coleta falhou", extra={"competitor": collector.competitor_slug})
        return 1

    print(f"{collector.competitor_slug}: {len(files)} arquivo(s) salvo(s)")
    for file in files:
        print(f"  {file.local_path}  ({file.sha256[:12]})")
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

    if args.command == "importar":
        return _run(ManualInboxCollector(settings, args.concorrente))

    http = PoliteHttpClient(settings)
    try:
        return _run(WEB_COLLECTORS[args.fonte](settings, http))
    finally:
        http.close()


if __name__ == "__main__":
    sys.exit(main())
