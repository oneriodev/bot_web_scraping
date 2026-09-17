# -*- coding: utf-8 -*-
"""
main.py

Bot Web Scanner de Supermercados — versão simplificada.

Fluxo: para cada site em config/settings.py -> SITES, baixa o HTML da página
de ofertas e salva localmente as imagens de banner/encarte encontradas, em
output/imagens_capturadas/<nome_do_site>/. Não faz OCR, não usa LLM, não gera
Excel — só coleta as imagens.

Uso:
    python main.py
    python main.py --site "Rede Serve Mais"
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from config.settings import SITES
from scraper.html_scraper import HtmlScraper
from scraper.image_capture import ImageCapture
from utils.logger_config import configurar_logger

logger = configurar_logger("main")


def processar_site(site_config: dict) -> List[Path]:
    """Baixa o HTML do site e salva as imagens de oferta encontradas nele."""
    nome_site = site_config["nome"]
    url_alvo = site_config.get("url_ofertas", site_config["url_base"])

    logger.info("=" * 70)
    logger.info("Iniciando coleta no site: %s (%s)", nome_site, url_alvo)
    logger.info("=" * 70)

    imagens_baixadas: List[Path] = []

    try:
        scraper = HtmlScraper(site_config)
        html = scraper.obter_html(url_alvo)

        if not html:
            logger.error("Não foi possível obter o HTML de %s — pulando este site.", nome_site)
            return imagens_baixadas

        seletor_imagens = site_config.get("seletores", {}).get("imagens_oferta", "img")
        captura = ImageCapture(nome_site)
        imagens_baixadas = captura.capturar_e_baixar(html, url_alvo, seletor_imagens)

        if not imagens_baixadas:
            logger.warning("Nenhuma imagem de oferta encontrada em %s", nome_site)

    except Exception as erro:  # noqa: BLE001 - garante que um site com erro não derruba o bot
        logger.exception("Erro inesperado ao processar o site '%s': %s", nome_site, erro)

    logger.info("Coleta finalizada em '%s': %d imagem(ns) baixada(s)", nome_site, len(imagens_baixadas))
    return imagens_baixadas


def executar_bot(filtro_site: str | None = None) -> None:
    """Percorre todos os sites configurados (ou apenas um) e baixa as imagens de oferta."""
    logger.info("Bot Web Scanner de Supermercados — iniciando execução")

    sites_a_processar = SITES
    if filtro_site:
        sites_a_processar = [s for s in SITES if s["nome"].lower() == filtro_site.lower()]
        if not sites_a_processar:
            logger.error(
                "Nenhum site encontrado com o nome '%s'. Sites disponíveis: %s",
                filtro_site, ", ".join(s["nome"] for s in SITES),
            )
            return

    total_imagens = 0
    for site_config in sites_a_processar:
        imagens = processar_site(site_config)
        total_imagens += len(imagens)

    logger.info(
        "Execução concluída. %d imagem(ns) salva(s) em output/imagens_capturadas/",
        total_imagens,
    )


def _parse_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bot Web Scanner de imagens de ofertas de supermercados."
    )
    parser.add_argument(
        "--site", type=str, default=None,
        help="Nome exato de um site específico (conforme config/settings.py) para "
             "rodar apenas ele. Se omitido, roda todos os sites configurados.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    argumentos = _parse_argumentos()
    try:
        executar_bot(filtro_site=argumentos.site)
    except KeyboardInterrupt:
        logger.warning("Execução interrompida manualmente pelo usuário.")
    except Exception as erro:  # noqa: BLE001 - captura qualquer erro não tratado no nível raiz
        logger.exception("Erro fatal na execução do bot: %s", erro)
