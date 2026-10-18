"""Inspeção de página DINÂMICA com Playwright (Etapa 8 — Assaí).

Uso:
    uv run python scripts/inspecionar_dinamico.py "https://www.assai.com.br/ofertas/mato-grosso/assai-jardim-das-americas"

Abre a página UMA vez num Chromium sem janela, espera o JavaScript montar o
conteúdo e mostra:
  1. chamadas de dados (XHR/fetch/JSON) — possível "atalho" sem navegador;
  2. imagens baixadas pela página (maiores primeiro);
  3. <img> presentes no DOM depois do JavaScript (inclusive escondidas);
  4. botões que parecem setas de carrossel.
Não clica em nada. Salva o HTML renderizado em ~/inspecao_renderizada.html.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Response, sync_playwright

EXTRA_WAIT_MS = 8000  # tempo para o JavaScript buscar e montar os encartes
NOISE = (
    "google", "facebook", "doubleclick", "hotjar", "clarity", "tiktok",
    "gtm", "analytics", "pixel", "onetrust", "cookiebot", "youtube",
)  # fmt: skip

# JavaScript executado DENTRO da página: devolve dados de cada <img>.
JS_IMAGES = """
els => els.map(e => ({
    src: e.currentSrc || e.src || '',
    lazy: e.getAttribute('data-src') || e.getAttribute('data-lazy') || e.getAttribute('data-original') || '',
    width: e.naturalWidth,
    height: e.naturalHeight,
    visible: !!(e.offsetWidth || e.offsetHeight),
    parent: (e.parentElement && e.parentElement.className) || '',
    alt: e.alt || ''
}))
"""

JS_BUTTONS = """
els => els
  .map(e => ({tag: e.tagName, cls: e.className || '', aria: e.getAttribute('aria-label') || '',
              text: (e.innerText || '').trim().slice(0, 30)}))
  .filter(b => /next|prox|próx|seta|arrow|slick|swiper|owl|carousel|avan/i
                 .test(b.cls + ' ' + b.aria + ' ' + b.text))
"""


def is_noise(url: str) -> bool:
    return any(word in url.lower() for word in NOISE)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    url = sys.argv[1]
    data_calls: list[str] = []
    downloaded: list[tuple[int, str]] = []

    def on_response(response: Response) -> None:
        """Chamado para CADA resposta que a página recebe (como a aba Network)."""
        kind = response.request.resource_type
        ctype = response.headers.get("content-type", "")
        if is_noise(response.url):
            return
        if kind in ("xhr", "fetch") or "json" in ctype:
            data_calls.append(
                f"{response.status} {response.request.method} {response.url}  [{ctype}]"
            )
        elif kind == "image":
            size = int(response.headers.get("content-length") or 0)
            downloaded.append((size, response.url))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="pt-BR")
        page.on("response", on_response)
        page.goto(url, wait_until="load", timeout=60_000)
        page.wait_for_timeout(EXTRA_WAIT_MS)

        images = page.eval_on_selector_all("img", JS_IMAGES)
        buttons = page.eval_on_selector_all("button, a, div[role=button], span", JS_BUTTONS)
        html = page.content()
        browser.close()

    output = Path.home() / "inspecao_renderizada.html"
    output.write_text(html, encoding="utf-8")
    print(f"HTML renderizado salvo em {output} ({len(html):,} caracteres)")

    print(f"\n===== 1. CHAMADAS DE DADOS (XHR/fetch/JSON): {len(data_calls)} =====")
    for line in data_calls:
        print(" ", line)

    print(f"\n===== 2. IMAGENS BAIXADAS (maiores primeiro): {len(downloaded)} =====")
    for size, image_url in sorted(downloaded, reverse=True)[:20]:
        print(f"  {size / 1024:8.0f} KB  {image_url}")

    print(f"\n===== 3. <img> NO DOM DEPOIS DO JAVASCRIPT: {len(images)} =====")
    for img in images:
        if img["width"] < 300 and not img["lazy"]:
            continue  # ícones e logos: fora
        print(
            f"  {img['width']}x{img['height']} visível={img['visible']} pai='{img['parent'][:40]}'\n"
            f"     src : {img['src']}\n"
            f"     lazy: {img['lazy'] or '-'}   alt: {img['alt'][:60]}"
        )

    print(f"\n===== 4. POSSÍVEIS SETAS DE CARROSSEL: {len(buttons)} =====")
    for button in buttons[:10]:
        print(
            f"  <{button['tag']}> class='{button['cls'][:60]}' aria='{button['aria']}' texto='{button['text']}'"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
