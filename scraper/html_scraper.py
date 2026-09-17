# -*- coding: utf-8 -*-
"""
scraper/html_scraper.py

Módulo responsável por baixar o HTML de uma página de ofertas:
    - via requests, quando o site é estático
    - via Selenium (navegador headless), quando o conteúdo é montado por
      JavaScript (SPA, Elementor, React, Vue, etc.)

O HTML baixado aqui é repassado para scraper/image_capture.py, que localiza e
baixa as imagens de banner/encarte dentro dele.
"""

from __future__ import annotations

from typing import Optional

import requests

from config.settings import (
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
    SELENIUM_HEADLESS,
    SELENIUM_IMPLICIT_WAIT,
    SELENIUM_PAGE_LOAD_TIMEOUT,
)
from utils.logger_config import configurar_logger

logger = configurar_logger(__name__)


class HtmlScraper:
    """Baixa o HTML de páginas de supermercado, estáticas ou dinâmicas."""

    def __init__(self, site_config: dict):
        self.site_config = site_config
        self.nome_site = site_config.get("nome", "site_desconhecido")

    def baixar_html_estatico(self, url: str) -> Optional[str]:
        """Baixa o HTML via requests (sem executar JavaScript)."""
        try:
            logger.info("Baixando HTML estático de: %s", url)
            resposta = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resposta.raise_for_status()
            resposta.encoding = resposta.apparent_encoding
            return resposta.text
        except requests.exceptions.RequestException as erro:
            logger.error("Falha ao baixar HTML estático de %s: %s", url, erro)
            return None

    def baixar_html_dinamico(self, url: str) -> Optional[str]:
        """
        Baixa o HTML renderizado via Selenium (necessário quando o conteúdo é
        montado dinamicamente via JavaScript).

        Importa o Selenium apenas aqui dentro para não obrigar a instalação/uso
        de um webdriver quando o site não precisa dele.
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
        except ImportError as erro:
            logger.error(
                "Selenium/webdriver-manager não instalados. Rode: "
                "pip install selenium webdriver-manager. Detalhe: %s", erro
            )
            return None

        options = Options()
        if SELENIUM_HEADLESS:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"user-agent={REQUEST_HEADERS['User-Agent']}")

        driver = None
        try:
            logger.info("Iniciando navegador headless para renderizar: %s", url)
            servico = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=servico, options=options)
            driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)
            driver.implicitly_wait(SELENIUM_IMPLICIT_WAIT)
            driver.get(url)
            html = driver.page_source
            logger.info("Página renderizada com sucesso: %s", url)
            return html
        except Exception as erro:  # noqa: BLE001 - queremos capturar qualquer falha do driver
            logger.error("Falha ao renderizar página dinâmica %s: %s", url, erro)
            return None
        finally:
            if driver is not None:
                driver.quit()

    def obter_html(self, url: str) -> Optional[str]:
        """
        Decide automaticamente entre requests e Selenium, conforme a configuração
        do site ("requer_selenium").
        """
        if self.site_config.get("requer_selenium"):
            return self.baixar_html_dinamico(url)
        return self.baixar_html_estatico(url)
