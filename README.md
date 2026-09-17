# Bot Web Scanner de Supermercados — Coleta de Imagens

Script Python que acessa a página de ofertas de sites de supermercado e baixa
localmente as imagens de banner/encarte encontradas nela. Não faz OCR, não usa
LLM, não gera Excel — a saída do bot são as próprias imagens.

## 1. Estrutura do projeto

```
supermarket_scanner/
├── main.py                      # ponto de entrada
├── requirements.txt
├── config/
│   └── settings.py              # lista de sites e parâmetros gerais
├── scraper/
│   ├── html_scraper.py          # requests + Selenium (baixa o HTML da página)
│   └── image_capture.py         # localiza e baixa as imagens de oferta
├── utils/
│   └── logger_config.py         # logging (console + arquivo com rotação diária)
├── logs/                        # gerado automaticamente
└── output/
    └── imagens_capturadas/      # imagens baixadas, uma pasta por site
```

## 2. Instalação

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
```

Também é necessário ter o **Google Chrome** instalado (usado pelo Selenium
para renderizar páginas com conteúdo dinâmico via JavaScript).

## 3. Como rodar

```bash
python main.py
```

Para rodar apenas um site específico (nome exato conforme `config/settings.py`):

```bash
python main.py --site "Rede Serve Mais"
```

As imagens baixadas ficam em `output/imagens_capturadas/<nome_do_site>/`.
Logs de execução ficam em `logs/bot_scanner.log`.

## 4. Adicionando um novo site

Edite `config/settings.py -> SITES` e adicione uma nova entrada:

```python
{
    "nome": "Nome do Supermercado",
    "url_base": "https://exemplo.com/",
    "url_ofertas": "https://exemplo.com/ofertas",
    "requer_selenium": True,  # True se a página carrega conteúdo via JavaScript
    "seletores": {
        "imagens_oferta": "img",
