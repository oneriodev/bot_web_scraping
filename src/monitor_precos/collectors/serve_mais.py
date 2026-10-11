"""Coletor da Rede Serve Mais (redeservemais.com.br).

O site é um WordPress + Elementor: as imagens do encarte ficam em /wp-content/uploads/
e a página de ofertas mostra MINIATURAS (sufixo "-261x300"). O arquivo original tem o
mesmo caminho sem o sufixo.

Como a página mistura campanhas (a vigente e outras antigas que nunca foram tiradas
do ar), cada imagem recebe a CAMPANHA (tirada do nome do arquivo) e a VALIDADE:

  - A validade aparece como título dentro de um bloco ("De 23/09/2026 a 02/10/2026",
    "Em 04/04/2026 e 05/04/2026"). As imagens desse bloco ensinam qual validade vale
    para a campanha delas.
  - Imagens da mesma campanha em OUTROS blocos (ex.: seções Bebidas, Carnes) herdam
    essa validade pela campanha.
  - Campanha sem nenhuma validade na página fica com validade vazia (None): a Etapa 4
    tenta ler a data impressa no card, ou manda para revisão.

A coleta guarda TUDO (camada bruta); filtrar o que está vigente é papel do banco/painel.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

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

# Trechos de caminho que indicam imagem decorativa (logos, ícones, banners).
IGNORED_WORDS = (
    "logo", "icon", "favicon", "sprite", "banner", "facebook", "instagram",
    "tiktok", "linkedin", "whatsapp",
)  # fmt: skip

# Atributos onde sites com "lazy loading" escondem a URL real da imagem.
LAZY_ATTRIBUTES = ("src", "data-src", "data-lazy-src", "data-orig-file")

# "-261x300" logo antes da extensão: padrão das miniaturas do WordPress.
_THUMBNAIL_SUFFIX = re.compile(r"-\d+x\d+(?=\.[A-Za-z0-9]+$)")
# Final do nome que numera os cards de uma campanha: "-WEB_19", "-WEB2_12_1", "-web1".
_CARD_NUMBER_SUFFIX = re.compile(r"[-_]?web\d*(?:_\d+)*$", re.IGNORECASE)
# Texto de validade: "De 23/09/2026 a 02/10/2026" ou "Em 04/04/2026 e 05/04/2026".
_VALIDITY_TEXT = re.compile(r"\b(?:de|em)\b.*\d{2}/\d{2}/\d{4}", re.IGNORECASE)
_DATE = re.compile(r"\d{2}/\d{2}/\d{4}")


# ------------------------------------------------------------------ funções puras
def to_original_url(url: str) -> str:
    """Remove o sufixo de miniatura do WordPress: ...WEB_23-261x300.webp → ...WEB_23.webp"""
    parts = urlsplit(url)
    return parts._replace(path=_THUMBNAIL_SUFFIX.sub("", parts.path)).geturl()


def campaign_key(url: str) -> str:
    """Nome da campanha a partir do arquivo: ...out26-FORMATO-WEB_19.webp → ...out26-FORMATO"""
    stem = PurePosixPath(urlsplit(url).path).stem
    return _CARD_NUMBER_SUFFIX.sub("", stem) or stem


def parse_validity(text: str) -> tuple[date, date] | None:
    """Extrai (início, fim) de textos como "De 23/09/2026 a 02/10/2026". None se não houver."""
    if not _VALIDITY_TEXT.search(text):
        return None
    dates: list[date] = []
    for raw in _DATE.findall(text):
        try:
            dates.append(datetime.strptime(raw, "%d/%m/%Y").date())
        except ValueError:  # ex.: 31/02/2026
            continue
    return (min(dates), max(dates)) if dates else None


def local_filename(url: str) -> str:
    """Nome local com ano/mês do upload, para não colidir arquivos de meses diferentes.

    .../uploads/2026/09/Tabloide_23.webp → 2026_09_Tabloide_23.webp
    """
    parts = PurePosixPath(urlsplit(url).path).parts
    if "uploads" in parts:
        after_uploads = parts[parts.index("uploads") + 1 :]
        return "_".join(after_uploads)
    return parts[-1]


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


def _top_block(element: Tag) -> str:
    """data-id do container Elementor de nível mais alto que contém o elemento."""
    block = ""
    for parent in element.parents:
        if "e-parent" in (parent.get("class") or []):
            block = parent.get("data-id", "")
    return block


def _image_urls(img: Tag, page_url: str) -> list[str]:
    """URLs absolutas candidatas de uma <img> (src, lazy loading e maior do srcset)."""
    candidates = [img.get(attr) for attr in LAZY_ATTRIBUTES]
    if srcset := img.get("srcset") or img.get("data-srcset"):
        candidates.append(_largest_from_srcset(srcset))
    return [
        urljoin(page_url, raw.strip())
        for raw in candidates
        if raw and not raw.startswith("data:")  # "data:" = placeholder embutido
    ]


@dataclass(frozen=True, slots=True)
class OfferImage:
    """Uma imagem de oferta encontrada na página."""

    original_url: str  # URL do arquivo em resolução original
    page_url: str  # URL que aparece na página (plano B se o original não existir)
    campaign: str
    valid_from: date | None = None
    valid_until: date | None = None


def parse_offer_images(html: str, page_url: str) -> list[OfferImage]:
    """Extrai do HTML as imagens de oferta com campanha e validade.

    Função "pura" (não acessa rede nem disco): fácil de testar com um HTML de exemplo.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Cabeçalho, rodapé e menu nunca têm encarte; script/style não têm texto visível.
    for tag in soup.select("header, footer, nav, script, style"):
        tag.decompose()

    found: dict[str, tuple[str, str]] = {}  # original → (url na página, campanha)
    block_validity: dict[str, tuple[date, date]] = {}  # bloco → última validade vista
    campaign_validity: dict[str, tuple[date, date]] = {}  # campanha → validade

    # Percorre a página NA ORDEM em que os elementos aparecem.
    for node in soup.descendants:
        if isinstance(node, NavigableString) and not isinstance(node, Comment):
            validity = parse_validity(" ".join(node.split()))
            if validity and node.parent is not None:
                block_validity[_top_block(node.parent)] = validity

        elif isinstance(node, Tag) and node.name == "img":
            block = _top_block(node)
            for absolute in _image_urls(node, page_url):
                if not _is_offer_image(absolute):
                    continue
                original = to_original_url(absolute)
                campaign = campaign_key(original)
                found.setdefault(original, (absolute, campaign))  # 1ª ocorrência vence
                if block in block_validity:
                    # Esta imagem está num bloco com data: a campanha dela aprende a validade.
                    campaign_validity.setdefault(campaign, block_validity[block])

    images: list[OfferImage] = []
    for original, (on_page, campaign) in found.items():
        valid_from, valid_until = campaign_validity.get(campaign, (None, None))
        images.append(OfferImage(original, on_page, campaign, valid_from, valid_until))
    return images


# ------------------------------------------------------------------ coletor
class ServeMaisCollector(BaseCollector):
    """Baixa as imagens de oferta da Rede Serve Mais, com campanha e validade."""

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

            collected.append(
                self.save_file(
                    content,
                    local_filename(final_url),
                    final_url,
                    ContentType.IMAGE,
                    campaign=image.campaign,
                    valid_from=image.valid_from,
                    valid_until=image.valid_until,
                )
            )

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
