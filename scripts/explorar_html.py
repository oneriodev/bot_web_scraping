"""Mostra a "planta" de uma página de ofertas: títulos, datas e imagens na ordem do DOM.

Uso:
    uv run python scripts/explorar_html.py ~/ofertas_serve_mais.html

Para cada evento imprime o bloco-pai (data-id do container do Elementor), assim dá
para ver quais imagens pertencem a qual título/validade. Imagens seguidas do mesmo
bloco são agrupadas numa linha só.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

DATE_RANGE = re.compile(r"De \d{2}/\d{2}/\d{4} a \d{2}/\d{2}/\d{4}")
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def top_block(element: Tag) -> str:
    """data-id do container Elementor de nível mais alto que contém o elemento."""
    block = "?"
    for parent in element.parents:
        if isinstance(parent, Tag) and "e-parent" in (parent.get("class") or []):
            block = parent.get("data-id", "?")
    return block


def main(path: Path) -> None:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for tag in soup.select("header, footer, nav, script, style"):
        tag.decompose()

    pending_imgs: list[str] = []
    pending_block = ""

    def flush() -> None:
        if pending_imgs:
            first, last = pending_imgs[0], pending_imgs[-1]
            print(f"  [{pending_block}] IMG x{len(pending_imgs)}: {first} ... {last}")
            pending_imgs.clear()

    for node in soup.body.descendants:
        if isinstance(node, Tag) and node.name == "img":
            src = node.get("src") or node.get("data-src") or ""
            if "/wp-content/uploads/" not in src:
                continue
            block = top_block(node)
            if block != pending_block:
                flush()
                pending_block = block
            pending_imgs.append(src.rsplit("/", 1)[-1])
        elif isinstance(node, NavigableString):
            text = " ".join(node.split())
            parent = node.parent
            is_heading = parent is not None and (
                parent.name in HEADING_TAGS
                or "elementor-heading-title" in (parent.get("class") or [])
            )
            if text and (is_heading or DATE_RANGE.search(text)):
                flush()
                tag = parent.name if parent is not None else "?"
                print(f"[{top_block(node.parent)}] TEXTO <{tag}>: {text[:80]}")
    flush()


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser())
