"""Normalização de descrições e tamanhos para comparar produtos de fontes diferentes.

"Óleo de Soja 900ML" e "oleo soja 900 ml" precisam virar o MESMO texto antes de
qualquer comparação. Tudo aqui são funções puras (texto entra, texto sai).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

# Abreviações comuns em encartes e cadastros → forma por extenso/padronizada.
_ABBREVIATIONS = {
    "und": "un", "unid": "un", "unidade": "un", "unidades": "un",
    "lt": "l", "lts": "l", "litro": "l", "litros": "l",
    "gr": "g", "grs": "g", "grama": "g", "gramas": "g", "kgs": "kg",
    "pct": "pacote", "cx": "caixa", "fd": "fardo",
    "refri": "refrigerante", "refrig": "refrigerante",
    "trad": "tradicional", "tp": "tipo",
    "mussarela": "mucarela", "muzarela": "mucarela", "mozarela": "mucarela",
    "folhas": "fls", "folha": "fls",
}  # fmt: skip

# Palavras que não ajudam a identificar o produto.
_STOPWORDS = {"de", "da", "do", "das", "dos", "e", "em", "com", "para", "a", "o"}

# "c/ abas" → "com abas", "p/ bolo" → "para bolo", "s/ gas" → "sem gas"
_SLASH_ABBREVIATION = re.compile(r"\b([csp])/\s*")
_SLASH_WORDS = {"c": "com ", "s": "sem ", "p": "para "}

# Tamanho já normalizado: "900 ml", "1.5 l", "200 fls"
_SIZE = re.compile(r"\b\d+(?:\.\d+)?\s(?:kg|g|ml|l|un|fls)\b")

# Unidade → (multiplicador, unidade-base): 5 kg = 5000 g; 1,5 l = 1500 ml.
_TO_BASE: dict[str, tuple[Decimal, str]] = {
    "kg": (Decimal(1000), "g"),
    "g": (Decimal(1), "g"),
    "l": (Decimal(1000), "ml"),
    "ml": (Decimal(1), "ml"),
    "un": (Decimal(1), "un"),
}


def normalize_text(text: str | None) -> str:
    """Minúsculas, sem acento, abreviações expandidas, número separado da unidade."""
    if not text:
        return ""
    # "ç" → "c", "á" → "a": NFKD separa a letra do acento; o encode descarta o acento.
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = _SLASH_ABBREVIATION.sub(lambda match: _SLASH_WORDS[match.group(1)], text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)  # 1,5 → 1.5 (vírgula decimal)
    text = re.sub(r"[^a-z0-9.]+", " ", text)  # pontuação vira espaço
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # ponto que não é decimal some
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)  # 900ml → 900 ml
    tokens = (_ABBREVIATIONS.get(token, token) for token in text.split())
    return " ".join(token for token in tokens if token not in _STOPWORDS)


def strip_size(normalized: str) -> str:
    """Remove tamanhos do texto JÁ normalizado: "oleo soja 900 ml" → "oleo soja"."""
    return " ".join(_SIZE.sub(" ", normalized).split())


def base_size(quantity: Decimal | None, unit: str | None) -> tuple[Decimal, str] | None:
    """Converte para a unidade-base: (5, "kg") → (5000, "g"). None se desconhecido."""
    if quantity is None or unit not in _TO_BASE:
        return None
    multiplier, base_unit = _TO_BASE[unit]
    return Decimal(quantity) * multiplier, base_unit


def format_size(quantity: Decimal | None, unit: str | None) -> str:
    """(1.5, "l") → "1500ml"; desconhecido → "". Usado na chave e na tela."""
    size = base_size(quantity, unit)
    if size is None:
        return ""
    value, base_unit = size
    return f"{value.normalize():f}{base_unit}"  # normalize() tira zeros: 100.000 → 100


def offer_key(product: str, brand: str | None, quantity: Decimal | None, unit: str | None) -> str:
    """Chave estável de um produto do concorrente: nome + marca + tamanho, normalizados.

    É ela que fica salva no de-para: a mesma oferta na semana que vem gera a mesma
    chave e já chega vinculada, sem nova aprovação.
    """
    parts = [
        strip_size(normalize_text(product)),
        normalize_text(brand),
        format_size(quantity, unit),
    ]
    return " ".join(part for part in parts if part)
