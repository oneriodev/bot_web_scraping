"""Testes do coletor da Serve Mais com um HTML de exemplo — sem acessar o site."""

from datetime import date

import pytest
import requests

from monitor_precos.collectors.serve_mais import (
    OFFERS_URL,
    ServeMaisCollector,
    campaign_key,
    local_filename,
    parse_offer_images,
    parse_validity,
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


# ------------------------------------------------------------------ campanha e validade
@pytest.mark.parametrize(
    ("arquivo", "campanha"),
    [
        ("Tabloides-fecha-mes-out26-FORMATO-WEB_19.webp", "Tabloides-fecha-mes-out26-FORMATO"),
        ("Dias-Mais-Abr2026-WEB2_12_1.webp", "Dias-Mais-Abr2026"),
        ("Mensal-Dezembro-WEB_90.webp", "Mensal-Dezembro"),
        ("Fecha-mes-3.png", "Fecha-mes-3"),
    ],
)
def test_campaign_key(arquivo: str, campanha: str) -> None:
    assert campaign_key(f"{UPLOADS}/{arquivo}") == campanha


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("De 23/09/2026 a 02/10/2026", (date(2026, 9, 23), date(2026, 10, 2))),
        ("Em 04/04/2026 e 05/04/2026", (date(2026, 4, 4), date(2026, 4, 5))),
        ("Bebidas", None),
        ("De 31/02/2026 a 01/03/2026", (date(2026, 3, 1), date(2026, 3, 1))),  # data inválida
    ],
)
def test_parse_validity(texto: str, esperado) -> None:
    assert parse_validity(texto) == esperado


# Estrutura real da página (resumida): bloco de destaques com data, bloco de evento
# antigo com data própria, e bloco de categorias SEM data com cards de duas campanhas.
OUT26 = "Tabloides-fecha-mes-out26-FORMATO"
HTML_REAL = f"""
<html><body>
<div class="e-con e-parent" data-id="9287ce6">
  <h2>OFERTAS ESPECIAIS</h2><h2>De 23/09/2026 a 02/10/2026</h2>
  <img src="{UPLOADS}/{OUT26}-WEB_19-261x300.webp">
</div>
<div class="e-con e-parent" data-id="ac61282">
  <img src="{UPLOADS}/Banner-web1.webp">
  <h2>Em 04/04/2026 e 05/04/2026</h2>
  <img src="{UPLOADS}/Dias-Mais-Abr2026-WEB_6.webp">
</div>
<div class="e-con e-parent" data-id="671e226">
  <h2>Bebidas</h2><img src="{UPLOADS}/{OUT26}-WEB_24-261x300.webp">
  <h2>Bazar</h2><img src="{UPLOADS}/Mensal-Dezembro-WEB_90.webp">
</div>
</body></html>
"""


def test_validade_por_campanha() -> None:
    por_arquivo = {
        img.original_url.rsplit("/", 1)[-1]: (img.campaign, img.valid_from, img.valid_until)
        for img in parse_offer_images(HTML_REAL, OFFERS_URL)
    }
    setembro = (date(2026, 9, 23), date(2026, 10, 2))
    abril = (date(2026, 4, 4), date(2026, 4, 5))

    assert por_arquivo == {
        f"{OUT26}-WEB_19.webp": (OUT26, *setembro),  # bloco com a data
        "Dias-Mais-Abr2026-WEB_6.webp": ("Dias-Mais-Abr2026", *abril),  # data do próprio bloco
        f"{OUT26}-WEB_24.webp": (OUT26, *setembro),  # herdou pela campanha (seção Bebidas)
        "Mensal-Dezembro-WEB_90.webp": ("Mensal-Dezembro", None, None),  # sem data: vazio
    }  # o banner foi ignorado


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
    collector = ServeMaisCollector(settings, http)
    files = collector.collect()
    assert [f.source_url for f in files] == [f"{UPLOADS}/B.webp"]
    assert collector.failed_urls == [f"{UPLOADS}/sumiu.webp"]  # a falha fica registrada


def test_coleta_grava_campanha_e_validade(settings) -> None:
    http = FakeHttp(
        HTML_REAL,
        {
            f"{UPLOADS}/{OUT26}-WEB_19.webp": b"card-19",
            f"{UPLOADS}/{OUT26}-WEB_24.webp": b"card-24",
        },
    )
    files = ServeMaisCollector(settings, http).collect()

    assert len(files) == 2  # os outros dois deram 404 e foram pulados sem derrubar a coleta
    assert all(f.campaign == OUT26 for f in files)
    assert all(f.valid_until == date(2026, 10, 2) for f in files)


def test_coleta_descarta_conteudo_repetido(settings) -> None:
    http = FakeHttp(
        _html_com(f"{UPLOADS}/A.webp", f"{UPLOADS}/A-copia.webp"),
        {f"{UPLOADS}/A.webp": b"igual", f"{UPLOADS}/A-copia.webp": b"igual"},
    )
    files = ServeMaisCollector(settings, http).collect()
    assert len(files) == 1
