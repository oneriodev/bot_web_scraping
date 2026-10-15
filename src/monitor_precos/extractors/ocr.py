"""Extrator por OCR clássico (Tesseract) — a ALTERNATIVA comparável ao LLM.

O Tesseract devolve TEXTO; quem decide o que é preço, peso e nome são as regras
(heurísticas) abaixo. Funcionam no caso comum e quebram no incomum — é justamente
isso que a avaliação (comando `avaliar`) mede.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal
from typing import Any

import pytesseract
from PIL import Image

from monitor_precos.extractors.base import BaseExtractor, ExtractionOutput
from monitor_precos.extractors.preprocess import prepare_for_ocr

# Teto de confiança: heurística de OCR nunca é tão confiável quanto ler a imagem
# "entendendo" o layout. Com o padrão de 0.8 para revisão, tudo do OCR vai para revisão.
OCR_MAX_CONFIDENCE = 0.7

# Peso/volume: "5KG", "900 ml", "1,5L", "2UN"...
_QUANTITY = re.compile(
    r"(?<![\d,.])(\d+(?:[.,]\d+)?)\s?(kgs?|gr?s?|ml|lts?|l|und?|unid)\b", re.IGNORECASE
)
# Preço: "17,99", "R$ 15.99". Limitação conhecida: "15 99" (centavos sem vírgula) não casa.
_PRICE = re.compile(r"(?<!\d)(\d{1,4})\s?[,.]\s?(\d{2})(?!\d)")
_LETTER = re.compile(r"[A-Za-zÀ-ÿ]")
_IGNORED_LINES = {"de", "por", "r$", "oferta", "ofertas", "cada", "apenas", "só"}


def group_lines(data: dict[str, list[Any]]) -> tuple[list[str], list[float]]:
    """Junta as palavras do Tesseract em linhas e devolve também a confiança de cada palavra."""
    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []
    for index, raw in enumerate(data["text"]):
        word = str(raw).strip()
        confidence = float(data["conf"][index])
        if not word or confidence < 0:  # -1 = não é palavra (bloco, linha vazia...)
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        lines.setdefault(key, []).append(word)
        confidences.append(confidence)
    return [" ".join(words) for words in lines.values()], confidences


def _is_product_line(line: str) -> bool:
    """Linha com cara de nome de produto: majoritariamente letras, sem preço."""
    if _PRICE.search(line) or "R$" in line.upper():
        return False
    letters = len(_LETTER.findall(line))
    characters = len(line.replace(" ", "")) or 1
    if letters < 3 or letters / characters < 0.6:
        return False
    return line.strip().lower() not in _IGNORED_LINES


def parse_ocr_lines(lines: list[str], confidence: float) -> list[dict[str, Any]]:
    """Heurística: UM card = UM produto. Menor preço = "por"; maior = "de"."""
    text = " ".join(lines)
    quantity = _QUANTITY.search(text)
    # Remove pesos antes de procurar preços, para "1,50 kg" não virar preço.
    prices = sorted(
        {Decimal(f"{int(whole)}.{cents}") for whole, cents in _PRICE.findall(_QUANTITY.sub(" ", text))}
    )  # fmt: skip
    if not prices:
        return []  # sem preço não há oferta

    product_lines = [_QUANTITY.sub(" ", line).strip() for line in lines if _is_product_line(line)]
    product = " ".join(" ".join(product_lines[:2]).split()) or None

    return [
        {
            "product": product,
            "brand": None,  # OCR não sabe separar marca de nome do produto
            "quantity": quantity.group(1).replace(",", ".") if quantity else None,
            "unit": quantity.group(2) if quantity else None,
            "regular_price": str(prices[-1]) if len(prices) > 1 else None,
            "promo_price": str(prices[0]),
            "special_condition": None,
            "valid_from": None,
            "valid_until": None,
            "confidence": confidence,
        }
    ]


class TesseractExtractor(BaseExtractor):
    def __init__(self, *, lang: str = "por", psm: int = 6, scale: float = 2.0) -> None:
        self._lang = lang
        self._psm = psm  # 6 = "um bloco de texto uniforme" (bom para cards)
        self._scale = scale

    @property
    def engine(self) -> str:
        return "ocr:tesseract"

    def extract(self, image: bytes, mime_type: str) -> ExtractionOutput:
        with Image.open(io.BytesIO(image)) as original:
            prepared = prepare_for_ocr(original, self._scale)
        data = pytesseract.image_to_data(
            prepared,
            lang=self._lang,
            config=f"--psm {self._psm}",
            output_type=pytesseract.Output.DICT,
        )
        lines, confidences = group_lines(data)
        mean_confidence = sum(confidences) / len(confidences) / 100 if confidences else 0.0
        confidence = round(min(mean_confidence, OCR_MAX_CONFIDENCE), 3)

        items = parse_ocr_lines(lines, confidence)
        return ExtractionOutput(is_offer_image=bool(items), items=items)
