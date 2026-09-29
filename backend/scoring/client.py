"""Llamada a Claude con tool_choice forzado y validación del resultado."""
from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Real
from typing import Any

from backend.scoring.prompt import MAX_TOKENS, MODEL_ID, SCORE_TOOL, SYSTEM_PROMPT, TEMPERATURE

_client = None

# Prompt caching. El prefijo cacheado es tools → system (orden fijo de la API) y
# se arma una sola vez al importar: nada dinámico adentro, así es idéntico byte a
# byte entre llamadas. El contexto del piso va en messages, después del último
# breakpoint, y no se cachea. TTL por defecto: 5 minutos.
CACHE_CONTROL = {"type": "ephemeral"}
CACHED_TOOLS = [{**SCORE_TOOL, "cache_control": CACHE_CONTROL}]
CACHED_SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": CACHE_CONTROL}]
TOOL_CHOICE = {"type": "tool", "name": SCORE_TOOL["name"]}


class ScoringError(Exception):
    """Base de los errores de scoring."""


class LLMCallError(ScoringError):
    """Falló la llamada a la API (red, rate limit, 5xx, etc.)."""


class NoToolUseError(ScoringError):
    """La respuesta no trae un bloque tool_use de score_listing."""


class ScoreValidationError(ScoringError):
    """El tool_use vino, pero con campos faltantes o fuera de rango."""


@dataclass
class ScoreResponse:
    raw: dict[str, Any]
    input_tokens: int | None = None           # sin cachear (lo que va después del breakpoint)
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    def usage_details(self) -> dict[str, int]:
        """Usage para Langfuse; omite los valores que la API no devolvió."""
        details = {
            "input": self.input_tokens,
            "output": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }
        return {k: v for k, v in details.items() if isinstance(v, int)}


@dataclass
class ScoreResult:
    score: float
    reasoning: str
    red_flags: list[str] = field(default_factory=list)
    green_flags: list[str] = field(default_factory=list)


def get_client():
    """Cliente AsyncAnthropic singleton (un solo pool httpx por proceso).

    Se crea en el primer uso y no al importar, así los tests no lo necesitan.
    """
    global _client
    if _client is None:
        from anthropic import AsyncAnthropic

        _client = AsyncAnthropic()
    return _client


def build_request_params(user_message: str) -> dict[str, Any]:
    """Parámetros de messages.create. Solo `messages` cambia entre listings."""
    return {
        "model": MODEL_ID,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "system": CACHED_SYSTEM,
        "tools": CACHED_TOOLS,
        "tool_choice": TOOL_CHOICE,
        "messages": [{"role": "user", "content": user_message}],
    }


async def request_score(user_message: str, client=None) -> ScoreResponse:
    """Pide el score a Claude. Devuelve el input crudo del tool_use."""
    client = client or get_client()
    try:
        response = await client.messages.create(**build_request_params(user_message))
    except Exception as e:
        raise LLMCallError(str(e)) from e

    usage = getattr(response, "usage", None)
    for block in response.content:
        if block.type == "tool_use" and block.name == SCORE_TOOL["name"]:
            return ScoreResponse(
                raw=block.input,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None),
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None),
            )
    raise NoToolUseError("Claude no devolvió tool_use de score_listing")


def _validate_flags(raw: dict, key: str) -> list[str]:
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ScoreValidationError(f"'{key}' debe ser una lista de strings")
    return value


def validate_score_result(raw: Any) -> ScoreResult:
    """Valida el input del tool_use: campos requeridos, tipos y score entre 0 y 10."""
    if not isinstance(raw, dict):
        raise ScoreValidationError("el resultado no es un objeto")

    required = SCORE_TOOL["input_schema"]["required"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ScoreValidationError(f"faltan campos requeridos: {', '.join(missing)}")

    score = raw["score"]
    if isinstance(score, bool) or not isinstance(score, Real):
        raise ScoreValidationError(f"'score' no es numérico: {score!r}")
    if not 0 <= score <= 10:
        raise ScoreValidationError(f"'score' fuera de rango 0-10: {score}")

    reasoning = raw["reasoning"]
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ScoreValidationError("'reasoning' vacío o no es texto")

    return ScoreResult(
        score=float(score),
        reasoning=reasoning,
        red_flags=_validate_flags(raw, "red_flags"),
        green_flags=_validate_flags(raw, "green_flags"),
    )
