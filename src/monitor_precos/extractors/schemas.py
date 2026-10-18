"""Modelos Pydantic da extração.

Duas camadas:
  - LlmCard / LlmOffer: o formato PEDIDO ao LLM (só texto e número, que toda API entende).
  - ExtractedOffer: o modelo de DOMÍNIO, com as regras do negócio. É ele que decide se
    o que o LLM devolveu é aceitável antes de ir para o banco.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Unit = Literal["kg", "g", "l", "ml", "un"]

# Variações que aparecem nos encartes → unidade padronizada.
_UNIT_ALIASES: dict[str, Unit] = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "quilo": "kg", "quilos": "kg",
    "g": "g", "gr": "g", "grs": "g", "grama": "g", "gramas": "g",
    "l": "l", "lt": "l", "lts": "l", "litro": "l", "litros": "l",
    "ml": "ml",
    "un": "un", "und": "un", "unid": "un", "unidade": "un", "unidades": "un",
    "pct": "un", "pacote": "un", "fd": "un", "fardo": "un", "cx": "un",
}  # fmt: skip

_BR_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


def normalize_unit(value: Any) -> str | None:
    """Padroniza a unidade: "KG", "Kg.", "quilo" → "kg". Desconhecida → None."""
    if value is None or value == "":
        return None
    return _UNIT_ALIASES.get(str(value).strip().lower().rstrip("."))


def parse_brl(value: Any) -> Decimal | None:
    """Converte preço em reais para Decimal: "R$ 1.299,90" → Decimal("1299.90").

    Decimal (e nunca float) porque float erra centavos: 0.1 + 0.2 = 0.30000000000000004.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    if isinstance(value, int | float) and not isinstance(value, bool):
        return Decimal(str(value)).quantize(Decimal("0.01"))

    text = str(value).upper().replace("R$", "").replace(" ", "").strip()
    if not text:
        return None
    if "," in text:  # formato brasileiro: ponto separa milhar, vírgula separa centavos
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValueError(f"preço inválido: {value!r}") from error


class ExtractedOffer(BaseModel):
    """Uma oferta já validada, pronta para o banco."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    product: str = Field(min_length=2)
    brand: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    unit: Unit | None = None
    regular_price: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    promo_price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    special_condition: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    confidence: float = Field(ge=0, le=1)
    page: int = Field(default=0, ge=0)  # página do PDF (imagem = 0)
    box_2d: tuple[int, int, int, int] | None = None  # [ymin, xmin, ymax, xmax] de 0 a 1000

    # ---------------------------------------------------- validadores de campo
    # mode="before": roda ANTES da conversão de tipo, para limpar o valor bruto do LLM.
    @field_validator("regular_price", "promo_price", mode="before")
    @classmethod
    def _parse_price(cls, value: Any) -> Decimal | None:
        return parse_brl(value)

    @field_validator("quantity", mode="before")
    @classmethod
    def _parse_quantity(cls, value: Any) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value).replace(",", ".")).quantize(Decimal("0.001"))
        except InvalidOperation as error:
            raise ValueError(f"quantidade inválida: {value!r}") from error

    @field_validator("unit", mode="before")
    @classmethod
    def _normalize_unit(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        # Unidade desconhecida vira None em vez de recusar a oferta inteira.
        return _UNIT_ALIASES.get(str(value).strip().lower().rstrip("."))

    @field_validator("brand", "special_condition", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("valid_from", "valid_until", mode="before")
    @classmethod
    def _parse_date(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, str) and (match := _BR_DATE.fullmatch(value.strip())):
            day, month, year = (int(part) for part in match.groups())
            try:
                return date(year, month, day)
            except ValueError:  # ex.: 31/02 — data impossível: descarta só a data
                return None
        return value

    @field_validator("box_2d", mode="before")
    @classmethod
    def _parse_box(cls, value: Any) -> tuple[int, int, int, int] | None:
        """Caixa do Gemini. Inválida vira None: a oferta continua, só não terá recorte."""
        if not isinstance(value, list | tuple) or len(value) != 4:
            return None
        try:
            ymin, xmin, ymax, xmax = (min(max(int(v), 0), 1000) for v in value)
        except (TypeError, ValueError):
            return None
        if ymin >= ymax or xmin >= xmax:
            return None
        return ymin, xmin, ymax, xmax

    # ---------------------------------------------------- regra entre campos
    @model_validator(mode="after")
    def _check_consistency(self) -> ExtractedOffer:
        if self.regular_price is not None and self.regular_price <= self.promo_price:
            # "De" menor ou igual ao "Por" não é promoção: provável erro de leitura.
            self.regular_price = None
            self.confidence = min(self.confidence, 0.5)
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            self.valid_from, self.valid_until = self.valid_until, self.valid_from
        return self


# ------------------------------------------------------------ formato pedido ao LLM
# Todos os campos sem valor padrão (mas aceitando null): esquemas com "default" nem
# sempre são aceitos pelas APIs de saída estruturada.
class LlmOffer(BaseModel):
    product: str
    brand: str | None
    quantity: float | None
    unit: str | None
    regular_price: float | None
    promo_price: float | None
    special_condition: str | None
    valid_from: str | None
    valid_until: str | None
    confidence: float
    box_2d: list[int] | None


class LlmCard(BaseModel):
    is_offer_image: bool
    offers: list[LlmOffer]
