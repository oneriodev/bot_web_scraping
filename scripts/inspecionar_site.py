"""Inspeção de um e-commerce ANTES de escrever o coletor (Etapa 8).

Uso:
    uv run python scripts/inspecionar_site.py https://www.comper.com.br

Faz poucas requisições (robots, até 5 sitemaps e 1 página de produto), todas pelo
PoliteHttpClient: respeita o robots.txt e espera entre uma requisição e outra.
Salva o HTML do produto em ~/inspecao_produto.html para testes offline.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from monitor_precos.collectors.http_client import PoliteHttpClient, RobotsDisallowedError
from monitor_precos.config import get_settings

MAX_SITEMAPS = 5  # inspeção, não coleta: poucas requisições
PLATFORM_MARKERS = ("vtex", "__NEXT_DATA__", "__NUXT__", "shopify", "magento", "wix", "vipcommerce")


def section(title: str) -> None:
    print(f"\n{'=' * 10} {title} {'=' * 10}")


def fetch_xml(http: PoliteHttpClient, url: str) -> str:
    """Baixa um sitemap; descompacta se vier em gzip (começa com os bytes 1f 8b)."""
    content, _ = http.get_bytes(url)
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return content.decode("utf-8", errors="replace")


def locs(xml: str) -> list[str]:
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)


def first_segment(url: str) -> str:
    parts = [p for p in urlsplit(url).path.split("/") if p]
    return f"/{parts[0]}/" if parts else "/"


def inspect_sitemaps(http: PoliteHttpClient, robots: str) -> list[str]:
    section("SITEMAP")
    sitemaps = re.findall(r"(?im)^sitemap:\s*(\S+)", robots)
    print("Declarados no robots:", sitemaps or "nenhum")
    if not sitemaps:
        return []

    queue, urls, read = list(sitemaps), [], 0
    while queue and read < MAX_SITEMAPS:
        url = queue.pop(0)
        try:
            xml = fetch_xml(http, url)
        except Exception as error:  # inspeção: mostra o erro e segue
            print(f"  ✗ {url}: {error}")
            continue
        read += 1
        found = locs(xml)
        if "<sitemapindex" in xml:
            print(f"  Índice {url} → {len(found)} sitemaps filhos, por exemplo:")
            for child in found[:10]:
                print("    ", child)
            # Primeiro os que parecem de produto.
            queue = sorted(found, key=lambda u: "produt" not in u.lower()) + queue
        else:
            print(f"  {url} → {len(found)} URLs")
            urls += found

    if urls:
        print("\nURLs por tipo (1º trecho do caminho):")
        for segment, count in Counter(map(first_segment, urls)).most_common(10):
            print(f"  {segment:<25} {count}")
        with_query = sum("?" in u for u in urls)
        print(f"URLs com '?' (proibidas pelo robots): {with_query}")
    return urls


def inspect_product(http: PoliteHttpClient, url: str) -> None:
    section(f"PÁGINA DE PRODUTO: {url}")
    html = http.get_text(url)
    output = Path.home() / "inspecao_produto.html"
    output.write_text(html, encoding="utf-8")
    print(f"HTML salvo em {output} ({len(html):,} caracteres)")

    lower = html.lower()
    print(
        "Marcadores de plataforma:", [m for m in PLATFORM_MARKERS if m.lower() in lower] or "nenhum"
    )

    soup = BeautifulSoup(html, "html.parser")

    # 1) JSON-LD (schema.org) — o melhor cenário.
    blocks = soup.find_all("script", type="application/ld+json")
    print(f"\nBlocos JSON-LD: {len(blocks)}")
    for block in blocks:
        try:
            data = json.loads(block.string or "")
        except json.JSONDecodeError:
            print("  (bloco inválido)")
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            kind = item.get("@type") if isinstance(item, dict) else type(item).__name__
            print(f"  @type = {kind}")
            if kind == "Product":
                print("  " + json.dumps(item, ensure_ascii=False)[:1200])

    # 2) JSON embutido de frameworks (Next.js / Nuxt).
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        data = json.loads(next_data.string)
        print(f"\n__NEXT_DATA__ ({len(next_data.string):,} caracteres), chaves:", list(data))
        print("  pageProps:", list(data.get("props", {}).get("pageProps", {}))[:20])

    # 3) Metadados de preço (Open Graph / microdata).
    metas = soup.select(
        'meta[property*="price"], meta[itemprop*="price"], [itemprop="price"], meta[property="og:type"]'
    )
    print(
        "\nMetadados de preço:",
        [(m.get("property") or m.get("itemprop"), m.get("content")) for m in metas][:10],
    )

    # 4) Último recurso: preços visíveis no texto.
    prices = re.findall(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}", soup.get_text(" "))
    print(f"Preços visíveis ('R$ x,xx'): {len(prices)} → {prices[:8]}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    base = sys.argv[1].rstrip("/")
    settings = get_settings()
    http = PoliteHttpClient(settings)
    try:
        section("ROBOTS.TXT")
        robots = http.get_text(f"{base}/robots.txt")
        print(robots)

        urls = inspect_sitemaps(http, robots)
        products = [u for u in urls if "/produtos/" in u and "?" not in u]
        if products:
            inspect_product(http, products[0])
        else:
            print("\nNenhuma URL de /produtos/ encontrada nos sitemaps lidos.")
    except RobotsDisallowedError as error:
        print(f"Bloqueado pelo robots.txt: {error}")
        return 1
    finally:
        http.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
