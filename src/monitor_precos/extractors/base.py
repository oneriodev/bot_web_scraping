"""Interface comum aos extratores (LLM, OCR...).

Igual aos coletores: o resto do pipeline só conhece `BaseExtractor`, então trocar
Gemini por Claude, Hugging Face ou Tesseract é criar uma classe nova.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Mapeamento próprio: o módulo `mimetypes` do Python nem sempre conhece .webp.
MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def mime_type_for(path: Path) -> str | None:
    """Tipo MIME da imagem pela extensão (None se não for imagem suportada)."""
    return MIME_TYPES.get(path.suffix.lower())


class ExtractionError(Exception):
    """O extrator não conseguiu uma resposta utilizável (bloqueio, resposta vazia...)."""


@dataclass(frozen=True, slots=True)
class ExtractionOutput:
    """Resposta BRUTA do extrator — ainda não validada pelo Pydantic."""

    is_offer_image: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def merge_outputs(outputs: list[ExtractionOutput]) -> ExtractionOutput:
    """Junta as respostas de várias páginas (PDF) numa só."""
    return ExtractionOutput(
        is_offer_image=any(output.is_offer_image for output in outputs),
        items=[item for output in outputs for item in output.items],
        input_tokens=sum(output.input_tokens for output in outputs),
        output_tokens=sum(output.output_tokens for output in outputs),
    )


class BaseExtractor(ABC):
    @property
    @abstractmethod
    def engine(self) -> str:
        """Identificação gravada no banco, ex.: "llm:gemini-3.8-flash"."""

    @abstractmethod
    def extract(self, image: bytes, mime_type: str) -> ExtractionOutput:
        """Lê uma imagem e devolve as ofertas encontradas (brutas)."""
