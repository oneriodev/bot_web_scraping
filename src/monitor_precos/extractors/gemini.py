"""Extrator usando a API do Gemini (Google) com saída estruturada em JSON."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from google import genai
from google.genai import errors, types

from monitor_precos.extractors.base import BaseExtractor, ExtractionError, ExtractionOutput
from monitor_precos.extractors.prompts import SYSTEM_PROMPT, USER_PROMPT
from monitor_precos.extractors.schemas import LlmCard

logger = logging.getLogger(__name__)

# Erros TEMPORÁRIOS: vale tentar de novo depois de esperar.
#   429 = limite de requisições por minuto | 5xx = servidor sobrecarregado ou instável
# Erros como 400 (pedido inválido), 403 (chave sem permissão) e 404 (modelo inexistente)
# NÃO entram: tentar de novo não resolve, então falham na hora.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GeminiExtractor(BaseExtractor):
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_attempts: int = 4,
        base_wait_seconds: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_attempts = max_attempts
        self._base_wait = base_wait_seconds
        self._sleep = sleep  # injetável: nos testes não precisa esperar de verdade

    @property
    def engine(self) -> str:
        return f"llm:{self._model}"

    def extract(self, image: bytes, mime_type: str) -> ExtractionOutput:
        response = self._generate_with_retry(image, mime_type)

        if not response.text:
            raise ExtractionError("Resposta vazia do Gemini (conteúdo bloqueado ou erro).")
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as error:
            raise ExtractionError(f"Gemini não devolveu JSON válido: {error}") from error

        usage = response.usage_metadata
        return ExtractionOutput(
            is_offer_image=bool(data.get("is_offer_image", True)),
            items=list(data.get("offers") or []),
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
        )

    def _generate_with_retry(self, image: bytes, mime_type: str) -> types.GenerateContentResponse:
        """Chama a API; em erro temporário espera 10 s, 20 s, 40 s... (backoff exponencial)."""
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._client.models.generate_content(
                    model=self._model,
                    contents=[types.Part.from_bytes(data=image, mime_type=mime_type), USER_PROMPT],
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0,  # o mais determinístico possível
                        response_mime_type="application/json",
                        response_schema=LlmCard,  # SAÍDA ESTRUTURADA: segue este esquema
                    ),
                )
            except errors.APIError as error:
                last_attempt = attempt == self._max_attempts
                if error.code not in RETRYABLE_STATUS or last_attempt:
                    raise
                wait = self._base_wait * 2 ** (attempt - 1)
                logger.warning(
                    "Gemini indisponível (%s), nova tentativa em %.0f s (%d/%d)",
                    error.code,
                    wait,
                    attempt + 1,
                    self._max_attempts,
                )
                self._sleep(wait)
        raise ExtractionError("Número de tentativas esgotado.")  # inalcançável na prática
