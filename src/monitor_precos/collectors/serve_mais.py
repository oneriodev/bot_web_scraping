"""Coletor da Rede Serve Mais (redeservemais.com.br).

O site é um WordPress: as imagens do encarte ficam em /wp-content/uploads/ e a
página de ofertas mostra MINIATURAS (sufixo "-261x300"). O arquivo original, com
resolução suficiente para extração, tem o mesmo caminho sem o sufixo.

Fluxo:
    1. Baixa o HTML da página de ofertas (o HTML puro já traz as URLs; sem Selenium).
    2. Extrai as URLs de imagem de oferta (src, data-src, srcset...).
    3. Converte cada miniatura para a URL do original.
    4. Baixa o original; se ele não existir (404), usa a miniatura como plano B.
    5. Ignora imagens repetidas na mesma coleta (mesmo hash SHA-256).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from monitor_precos.collectors.base import (
    BaseCollector,
    CollectedFile,
    ContentType,
    compute_sha256,
)
from monitor_precos.collectors.http_client import PoliteHttpClient
from monitor_precos.config import Settings

logger = logging.getLogger(__name__)

OFFERS_URL = "https://redeservemais.com.br/ofertas_2/"

VALID_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# Trechos de caminho que indicam imagem decorativa (logos, ícones, redes sociais).
IGNORED_WORDS = (
    "logo", "icon", "favicon", "sprite", "facebook", "instagram",
    "tiktok", "linkedin", "whatsapp",
)  # fmt: skip

# Atributos onde sites com "lazy loading" escondem a URL real da imagem.
LAZY_ATTRIBUTES = ("src", "data-src", "data-lazy-src", "data-orig-file")

# "-261x300" logo antes da extensão: padrão das miniaturas do WordPress.
_THUMBNAIL_SUFFIX = re.compile(r"-\d+x\d+(?=\.[A-Za-z0-9]+$)")


def to_original_url(url: str) -> str:
    """Remove o sufixo de miniatura do WordPress: ...WEB_23-261x300.webp → ...WEB_23.webp"""
    parts = urlsplit(url)
    return parts._replace(path=_THUMBNAIL_SUFFIX.sub("", parts.path)).geturl()


def _largest_from_srcset(srcset: str) -> str | None:
    """srcset = "a.webp 261w, b.webp 768w" → devolve a de maior largura ("b.webp")."""
    best_url, best_width = None, -1
    for candidate in srcset.split(","):
        pieces = candidate.strip().split()
        if not pieces:
            continue
        width = int(pieces[1][:-1]) if len(pieces) > 1 and pieces[1].endswith("w") else 0
        if width > best_width:
            best_url, best_width = pieces[0], width
    return best_url


def _is_offer_image(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return (
        "/wp-content/uploads/" in path
        and path.endswith(VALID_EXTENSIONS)
        and not any(word in path for word in IGNORED_WORDS)
    )


def local_filename(url: str) -> str:
    """Nome local com ano/mês do upload, para não colidir arquivos de meses diferentes.

    .../uploads/2026/09/Tabloide_23.webp → 2026_09_Tabloide_23.webp
    """
    parts = PurePosixPath(urlsplit(url).path).parts
    if "uploads" in parts:
        after_uploads = parts[parts.index("uploads") + 1 :]
        return "_".join(after_uploads)
    return parts[-1]


@dataclass(frozen=True, slots=True)
class OfferImage:
    """Uma imagem de oferta: a URL do original e a URL que aparece na página (plano B)."""

    original_url: str
    page_url: str


def parse_offer_images(html: str, page_url: str) -> list[OfferImage]:
    """Extrai do HTML as imagens de oferta, já com a URL do original calculada.

    Função "pura" (não acessa rede nem disco): fácil de testar com um HTML de exemplo.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Cabeçalho, rodapé e menu se repetem em todas as páginas e nunca têm encarte.
    for tag in soup.select("header, footer, nav"):
        tag.decompose()

    found: dict[str, OfferImage] = {}  # chave = URL do original (evita duplicadas)
    for img in soup.find_all("img"):
        candidates = [img.get(attr) for attr in LAZY_ATTRIBUTES]
        if srcset := img.get("srcset") or img.get("data-srcset"):
            candidates.append(_largest_from_srcset(srcset))

        for raw in candidates:
            if not raw or raw.startswith("data:"):  # "data:" = placeholder embutido
                continue
            absolute = urljoin(page_url, raw.strip())
            if _is_offer_image(absolute):
                original = to_original_url(absolute)
                # setdefault guarda só a 1ª ocorrência, mantendo a ordem da página.
                found.setdefault(original, OfferImage(original, absolute))

    return list(found.values())


class ServeMaisCollector(BaseCollector):
    """Baixa as imagens do encarte vigente da Rede Serve Mais."""

    competitor_slug = "serve_mais"
    competitor_name = "Rede Serve Mais"

    def __init__(self, settings: Settings, http: PoliteHttpClient) -> None:
        super().__init__(settings)
        self.http = http

    def collect(self) -> list[CollectedFile]:
        html = self.http.get_text(OFFERS_URL)
        images = parse_offer_images(html, OFFERS_URL)
        logger.info(
            "Imagens de oferta encontradas",
            extra={"competitor": self.competitor_slug, "total": len(images)},
        )

        collected: list[CollectedFile] = []
        seen_hashes: set[str] = set()

        for image in images:
            try:
                content, final_url = self._download(image)
            except requests.RequestException as error:
                # Uma imagem com problema não derruba a coleta inteira.
                logger.warning(
                    "Falha ao baixar imagem",
                    extra={"url": image.original_url, "error": str(error)},
                )
                continue

            digest = compute_sha256(content)
            if digest in seen_hashes:
                continue  # mesma imagem publicada com outro nome: não salva de novo
            seen_hashes.add(digest)

            file = self.save_file(content, local_filename(final_url), final_url, ContentType.IMAGE)
            collected.append(file)

        logger.info(
            "Coleta concluída",
            extra={"competitor": self.competitor_slug, "saved": len(collected)},
        )
        return collected

    def _download(self, image: OfferImage) -> tuple[bytes, str]:
        """Tenta o original; se ele não existir (404), usa a imagem da página como plano B."""
        try:
            content, _ = self.http.get_bytes(image.original_url)
            return content, image.original_url
        except requests.HTTPError as error:
            is_404 = error.response is not None and error.response.status_code == 404
            if not is_404 or image.page_url == image.original_url:
                raise
        logger.info("Original indisponível, usando miniatura", extra={"url": image.page_url})
        content, _ = self.http.get_bytes(image.page_url)
        return content, image.page_url
