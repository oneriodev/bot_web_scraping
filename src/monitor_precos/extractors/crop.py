"""Recorte de uma oferta dentro do encarte, para EXIBIR na tela.

O Gemini devolve a caixa de cada oferta em [ymin, xmin, ymax, xmax], de 0 a 1000
(proporção da imagem). Aqui viramos isso em pixels e recortamos com o Pillow.
Nada é gravado em disco: o recorte é feito na hora de mostrar.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

PREVIEW_DPI = 150  # PDF para tela: não precisa da resolução da extração (200)
MARGIN = 0.02  # 2% de folga em volta da caixa, para não cortar o preço rente


def page_image(path: Path, page: int = 0) -> Image.Image:
    """A imagem da página: o próprio arquivo, ou a página `page` do PDF renderizada."""
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(str(path)) as document:
            index = min(max(page, 0), document.page_count - 1)
            png = document[index].get_pixmap(dpi=PREVIEW_DPI).tobytes("png")
        return Image.open(io.BytesIO(png))
    return Image.open(path)


def crop_box(
    image: Image.Image, box: tuple[int, int, int, int], margin: float = MARGIN
) -> Image.Image:
    """Recorta a caixa normalizada (0–1000) com uma pequena margem."""
    ymin, xmin, ymax, xmax = box
    width, height = image.size
    left = max(xmin / 1000 - margin, 0) * width
    top = max(ymin / 1000 - margin, 0) * height
    right = min(xmax / 1000 + margin, 1) * width
    bottom = min(ymax / 1000 + margin, 1) * height
    return image.crop((round(left), round(top), round(right), round(bottom)))


def offer_image(
    path: Path, page: int | None = 0, box: tuple[int, int, int, int] | None = None
) -> Image.Image:
    """Card recortado se houver caixa; senão a página inteira."""
    image = page_image(path, page or 0)
    return crop_box(image, box) if box else image
