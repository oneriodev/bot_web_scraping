# -*- coding: utf-8 -*-
"""
config/settings.py

Arquivo central de configuração do Bot Web Scanner de Supermercados.

Escopo atual do bot: acessar a página de ofertas de cada site configurado e
baixar as imagens de banner/encarte encontradas nela. Não faz OCR, não usa
LLM, não gera Excel — só coleta as imagens.
"""

from pathlib import Path

# --------------------------------------------------------------------------------------
# Diretórios do projeto
# --------------------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"
IMAGES_DIR = OUTPUT_DIR / "imagens_capturadas"

for _dir in (OUTPUT_DIR, LOG_DIR, IMAGES_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------------------
# Sites-alvo. Estrutura modular: basta adicionar uma nova entrada em SITES para
# incluir outro supermercado, sem alterar o restante do código.
# --------------------------------------------------------------------------------------
SITES = [
    {
        "nome": "Rede Serve Mais",
        "url_base": "https://redeservemais.com.br/",
        "url_ofertas": "https://redeservemais.com.br/ofertas_2/",
        "requer_selenium": True,   # página carrega conteúdo via JS (Elementor)
        "seletores": {
            "imagens_oferta": "img",
        },
    },
    # Para adicionar um novo supermercado, copie o bloco acima e ajuste os campos.
]

# --------------------------------------------------------------------------------------
# Parâmetros gerais de execução
# --------------------------------------------------------------------------------------
REQUEST_TIMEOUT = 15          # segundos, para requests.get
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}

SELENIUM_HEADLESS = True
SELENIUM_PAGE_LOAD_TIMEOUT = 25
SELENIUM_IMPLICIT_WAIT = 5
