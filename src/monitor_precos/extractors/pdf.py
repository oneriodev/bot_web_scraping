"""Conversão de PDF em imagens (uma por página) para os extratores.

O PyMuPDF "desenha" cada página como PNG na resolução pedida (DPI). 200 DPI deixa
uma página A4 com ~1650×2340 pixels: suficiente para ler preços, sem exagerar no
tamanho (imagem maior = mais tokens = mais custo no LLM).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from monitor_precos.extractors.base import mime_type_for

DEFAULT_DPI = 200
MAX_PAGES = 20  # trava de segurança: encarte com 200 páginas é arquivo errado, não encarte


def pdf_to_png_pages(
    path: Path, *, dpi: int = DEFAULT_DPI, max_pages: int = MAX_PAGES
) -> list[bytes]:
    """Devolve o conteúdo PNG de cada página do PDF, em memória (sem criar arquivos)."""
    with pymupdf.open(str(path)) as document:
        if document.page_count > max_pages:
            raise ValueError(
                f"PDF com {document.page_count} páginas (máximo {max_pages}): confira o arquivo."
            )
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in document]


def load_pages(path: Path, content_type: str) -> list[tuple[bytes, str]]:
    """Imagens prontas para o extrator: 1 para imagem, N para PDF (uma por página)."""
    if content_type == "pdf":
        return [(png, "image/png") for png in pdf_to_png_pages(path)]
    mime_type = mime_type_for(path)
    if mime_type is None:
        raise ValueError(f"formato não suportado: {path.suffix}")
    return [(path.read_bytes(), mime_type)]
