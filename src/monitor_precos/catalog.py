"""Cadastro de produtos internos (a "nossa" loja) — a DIMENSÃO do modelo de dados.

No portfólio usamos dados FICTÍCIOS (dados_exemplo/produtos_ficticios.csv).
Dados reais da empresa ficam em dados_reais/ (no .gitignore) e NUNCA vão para o GitHub.

Formato do CSV (separador ";" ou ","; preços com vírgula):
    id;codigo_barras;descricao;marca;categoria;quantidade;unidade;preco_venda;vendas_mes
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from monitor_precos.config import PROJECT_ROOT
from monitor_precos.extractors.schemas import Unit, normalize_unit, parse_brl

EXAMPLE_PRODUCTS_CSV = PROJECT_ROOT / "dados_exemplo" / "produtos_ficticios.csv"


class InternalProduct(BaseModel):
    """Um produto do cadastro interno, já validado.

    validation_alias: o campo se chama `description` no código (inglês), mas é lido
    da coluna `descricao` do CSV (português).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    id: int = Field(gt=0)
    barcode: str | None = Field(default=None, validation_alias="codigo_barras")
    description: str = Field(min_length=3, validation_alias="descricao")
    brand: str | None = Field(default=None, validation_alias="marca")
    category: str = Field(min_length=2, validation_alias="categoria")
    quantity: Decimal | None = Field(default=None, gt=0, validation_alias="quantidade")
    unit: Unit | None = Field(default=None, validation_alias="unidade")
    sale_price: Decimal = Field(gt=0, validation_alias="preco_venda")
    monthly_sales: int = Field(ge=0, validation_alias="vendas_mes")

    @field_validator("barcode", "brand", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return None if value is None or str(value).strip() == "" else value

    @field_validator("barcode")
    @classmethod
    def _barcode_digits(cls, value: str | None) -> str | None:
        if value is not None and not (value.isdigit() and 8 <= len(value) <= 14):
            raise ValueError("código de barras deve ter de 8 a 14 dígitos")
        return value

    @field_validator("quantity", mode="before")
    @classmethod
    def _parse_quantity(cls, value: Any) -> Decimal | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            return Decimal(str(value).replace(",", ".")).quantize(Decimal("0.001"))
        except InvalidOperation as error:
            raise ValueError(f"quantidade inválida: {value!r}") from error

    @field_validator("unit", mode="before")
    @classmethod
    def _normalize_unit(cls, value: Any) -> str | None:
        return normalize_unit(value)

    @field_validator("sale_price", mode="before")
    @classmethod
    def _parse_price(cls, value: Any) -> Decimal | None:
        return parse_brl(value)

    @field_validator("monthly_sales", mode="before")
    @classmethod
    def _parse_sales(cls, value: Any) -> Any:
        return None if value is None or str(value).strip() == "" else value


def load_products_csv(path: Path) -> tuple[list[InternalProduct], list[str]]:
    """Lê e valida o CSV inteiro. Devolve (produtos válidos, erros com o número da linha)."""
    products: list[InternalProduct] = []
    errors: list[str] = []
    seen_ids: set[int] = set()

    with path.open(encoding="utf-8-sig", newline="") as file:
        header = file.readline()
        delimiter = ";" if header.count(";") >= header.count(",") else ","
        file.seek(0)
        # start=2: a linha 1 é o cabeçalho, então o primeiro produto está na linha 2.
        for line_number, row in enumerate(csv.DictReader(file, delimiter=delimiter), start=2):
            if not any(str(value or "").strip() for value in row.values()):
                continue  # linha em branco
            try:
                product = InternalProduct.model_validate(row)
            except ValidationError as error:
                first = error.errors()[0]
                field = ".".join(str(part) for part in first["loc"])
                errors.append(f"linha {line_number}: {field} — {first['msg']}")
                continue
            if product.id in seen_ids:
                errors.append(f"linha {line_number}: id {product.id} repetido")
                continue
            seen_ids.add(product.id)
            products.append(product)

    return products, errors
