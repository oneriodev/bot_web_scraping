# -*- coding: utf-8 -*-
"""
scraper/image_capture.py

Módulo responsável por localizar e baixar imagens relevantes de uma página
(banners de oferta, cards de produto, thumbnails de encarte). Este é o
resultado final do bot: as imagens salvas em disco, sem processamento
posterior (sem OCR, sem LLM, sem Excel).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config.settings import IMAGES_DIR, REQUEST_HEADERS, REQUEST_TIMEOUT
from utils.logger_config import configurar_logger

logger = configurar_logger(__name__)

# Extensões consideradas relevantes para leitura de encartes/banners.
EXTENSOES_VALIDAS = (".jpg", ".jpeg", ".png", ".webp")

# Palavras que costumam indicar imagens decorativas/irrelevantes (logos, ícones sociais).
PALAVRAS_IGNORAR = ("logo", "icon", "favicon", "sprite", "facebook", "instagram",
                    "tiktok", "linkedin", "whatsapp")


class ImageCapture:
    """Localiza e baixa imagens de ofertas/produtos de uma página de supermercado."""

    def __init__(self, nome_site: str):
        self.nome_site = nome_site
        self.pasta_destino = Path(IMAGES_DIR) / self._slug(nome_site)
        self.pasta_destino.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _slug(texto: str) -> str:
        return "".join(c.lower() if c.isalnum() else "_" for c in texto).strip("_")

    def listar_urls_imagens(self, html: str, url_base: str, seletor: str = "img") -> List[str]:
        """
        Varre o HTML em busca de tags <img> (ou outro seletor configurado) e
        retorna as URLs absolutas das imagens que parecem ser banners/ofertas
        relevantes.

        Antes de buscar, remove as seções de <header>, <footer> e <nav> do HTML
        — essas áreas se repetem em todas as páginas do site (logo, cartões,
        ícones de redes sociais) e nunca contêm os banners de oferta, então
        removê-las evita capturar imagens irrelevantes por engano, mesmo que o
        nome do arquivo não contenha nenhuma palavra da lista de exclusão.
        """
        soup = BeautifulSoup(html, "html.parser")

        for tag_irrelevante in soup.select("header, footer, nav"):
            tag_irrelevante.decompose()

        tags_imagem = soup.select(seletor)

        urls_encontradas = []
        for tag in tags_imagem:
            # Sites com carregamento "lazy" costumam usar data-src / data-lazy-src
            src = tag.get("src") or tag.get("data-src") or tag.get("data-lazy-src")
            if not src:
                continue

            url_absoluta = urljoin(url_base, src)
            nome_arquivo = urlparse(url_absoluta).path.lower()

            if not nome_arquivo.endswith(EXTENSOES_VALIDAS):
                continue
            if any(palavra in nome_arquivo for palavra in PALAVRAS_IGNORAR):
                continue

            urls_encontradas.append(url_absoluta)

        urls_unicas = sorted(set(urls_encontradas))
        logger.info(
            "Encontradas %d imagens candidatas a encarte/oferta em %s",
            len(urls_unicas), url_base,
        )
        return urls_unicas

    def baixar_imagens(self, urls: List[str]) -> List[Path]:
        """Baixa cada URL de imagem para a pasta local do site e retorna os caminhos salvos."""
        caminhos_salvos: List[Path] = []

        for url in urls:
            try:
                resposta = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
                resposta.raise_for_status()

                nome_arquivo = Path(urlparse(url).path).name or "imagem.jpg"
                caminho_local = self.pasta_destino / nome_arquivo

                with open(caminho_local, "wb") as arquivo:
                    arquivo.write(resposta.content)

                caminhos_salvos.append(caminho_local)
                logger.debug("Imagem salva: %s", caminho_local)

            except requests.exceptions.RequestException as erro:
                logger.warning("Falha ao baixar imagem %s: %s", url, erro)
                continue

        logger.info("Total de imagens baixadas com sucesso: %d", len(caminhos_salvos))
        return caminhos_salvos

    def capturar_e_baixar(self, html: str, url_base: str, seletor: str = "img") -> List[Path]:
        """Atalho: localiza e já baixa as imagens relevantes de uma página."""
        urls = self.listar_urls_imagens(html, url_base, seletor)
        return self.baixar_imagens(urls)
