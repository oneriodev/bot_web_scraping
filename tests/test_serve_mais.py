"""Testes do coletor da Serve Mais com um HTML de exemplo — sem acessar o site."""

import requests

from monitor_precos.collectors.serve_mais import (
    OFFERS_URL,
    ServeMaisCollector,
    local_filename,
    parse_offer_images,
    to_original_url,
)

UPLOADS = "https://redeservemais.com.br/wp-content/uploads/2026/09"

# HTML imitando a estrutura real: cabeçalho com logo, encarte com miniaturas,
# imagem com lazy loading, srcset, duplicada e um ícone de rede social.
HTML_EXEMPLO = f"""
<html><body>
  <header><img src="{UPLOADS}/banner-topo.webp"></header>
  <main>
    <img src="{UPLOADS}/Tabloide-WEB_1-261x300.webp">
    <img src="data:image/gif;base64,AAAA" data-src="{UPLOADS}/Tabloide-WEB_2-261x300.webp">
    <img src="{UPLOADS}/Tabloide-WEB_3-261x300.webp"
         srcset="{UPLOADS}/Tabloide-WEB_3-261x300.webp 261w, {UPLOADS}/Tabloide-WEB_3-768x883.webp 768w">
    <img src="{UPLOADS}/Tabloide-WEB_1-261x300.webp">
    <img src="{UPLOADS}/icone-whatsapp.png">
    <img src="https://outro-site.com/imagem.webp">
  </main>
  <footer><img src="{UPLOADS}/selo-rodape.webp"></footer>
</body></html>
"""  # noqa: E501


# ------------------------------------------------------------------ funções puras
def test_to_original_url_remove_sufixo_de_miniatura() -> None:
    assert to_original_url(f"{UPLOADS}/WEB_23-261x300.webp") == f"{UPLOADS}/WEB_23.webp"
    assert to_original_url(f"{UPLOADS}/Banner-web1.webp") == f"{UPLOADS}/Banner-web1.webp"


def test_local_filename_inclui_ano_e_mes() -> None:
    assert local_filename(f"{UPLOADS}/WEB_23.webp") == "2026_09_WEB_23.webp"


def test_parse_encontra_so_imagens_de_oferta() -> None:
    images = parse_offer_images(HTML_EXEMPLO, OFFERS_URL)
    originais = [img.original_url for img in images]

    assert originais == [
        f"{UPLOADS}/Tabloide-WEB_1.webp",  # miniatura → original
        f"{UPLOADS}/Tabloide-WEB_2.webp",  # veio do data-src (lazy loading)
        f"{UPLOADS}/Tabloide-WEB_3.webp",  # src e srcset apontam para o mesmo original
    ]
    # Ficaram de fora: header, footer, ícone, outro domínio, placeholder "data:" e duplicada.


def test_parse_guarda_url_da_pagina_como_plano_b() -> None:
    [primeira, *_] = parse_offer_images(HTML_EXEMPLO, OFFERS_URL)
    assert primeira.page_url == f"{UPLOADS}/Tabloide-WEB_1-261x300.webp"


# ------------------------------------------------------------------ coletor com HTTP falso
class FakeHttp:
    """Imita o PoliteHttpClient: responde a partir de dicionários, sem rede."""

    def __init__(self, html: str, files: dict[str, bytes]) -> None:
        self.html = html
        self.files = files
        self.requested: list[str] = []

    def get_text(self, url: str) -> str:
        return self.html

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        self.requested.append(url)
        if url not in self.files:
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError("404", response=response)
        return self.files[url], "image/webp"


def _html_com(*srcs: str) -> str:
    return "<main>" + "".join(f'<img src="{s}">' for s in srcs) + "</main>"


def test_coleta_baixa_originais(settings) -> None:
    http = FakeHttp(
        _html_com(f"{UPLOADS}/A-261x300.webp", f"{UPLOADS}/B-261x300.webp"),
        {f"{UPLOADS}/A.webp": b"imagem-A", f"{UPLOADS}/B.webp": b"imagem-B"},
    )
    files = ServeMaisCollector(settings, http).collect()

    assert [f.source_url for f in files] == [f"{UPLOADS}/A.webp", f"{UPLOADS}/B.webp"]
    assert files[0].local_path.name == "2026_09_A.webp"
    assert files[0].local_path.read_bytes() == b"imagem-A"
    assert all(f.competitor == "serve_mais" for f in files)


def test_coleta_usa_miniatura_quando_original_nao_existe(settings) -> None:
    miniatura = f"{UPLOADS}/A-261x300.webp"
    http = FakeHttp(_html_com(miniatura), {miniatura: b"miniatura-A"})

    [file] = ServeMaisCollector(settings, http).collect()

    assert http.requested == [f"{UPLOADS}/A.webp", miniatura]  # tentou o original antes
    assert file.source_url == miniatura


def test_coleta_ignora_imagem_com_erro_e_continua(settings) -> None:
    http = FakeHttp(
        _html_com(f"{UPLOADS}/sumiu.webp", f"{UPLOADS}/B.webp"),
        {f"{UPLOADS}/B.webp": b"imagem-B"},
    )
    files = ServeMaisCollector(settings, http).collect()
    assert [f.source_url for f in files] == [f"{UPLOADS}/B.webp"]


def test_coleta_descarta_conteudo_repetido(settings) -> None:
    http = FakeHttp(
        _html_com(f"{UPLOADS}/A.webp", f"{UPLOADS}/A-copia.webp"),
        {f"{UPLOADS}/A.webp": b"igual", f"{UPLOADS}/A-copia.webp": b"igual"},
    )
    files = ServeMaisCollector(settings, http).collect()
    assert len(files) == 1
