"""Selecciona proveedores de modelos de lenguaje y centraliza sus llamadas.

Los nodos del grafo usan esta interfaz común para crear modelos de Groq u
OpenAI y para reintentar errores temporales de límite de solicitudes.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Literal

from groq import RateLimitError as GroqRateLimitError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from openai import RateLimitError as OpenAIRateLimitError

from app.config import GROQ_API_KEY, OPENAI_API_KEY


LLMProvider = Literal["groq", "openai"]
LLMPurpose = Literal["general", "work_identity"]
# Ambas excepciones se tratan como límites reintentables; otros errores de API no se ocultan ni se reintentan por esta ruta.
RATE_LIMIT_ERRORS = (GroqRateLimitError, OpenAIRateLimitError)

# Modelos predeterminados por proveedor y propósito. Las variables específicas definidas abajo permiten reemplazarlos sin cambiar el código del grafo.
DEFAULT_MODELS: dict[LLMProvider, dict[LLMPurpose, str]] = {
    "groq": {
        "general": "openai/gpt-oss-120b",
        "work_identity": "qwen/qwen3.8-27b",
    },
    "openai": {
        "general": "gpt-5.4-mini",
        "work_identity": "gpt-5.4-mini",
    },
}

# Cada par proveedor/propósito apunta a su variable de entorno correspondiente.
MODEL_ENV_VARS: dict[LLMProvider, dict[LLMPurpose, str]] = {
    "groq": {
        "general": "GROQ_MODEL",
        "work_identity": "GROQ_WORK_IDENTITY_MODEL",
    },
    "openai": {
        "general": "OPENAI_MODEL",
        "work_identity": "OPENAI_WORK_IDENTITY_MODEL",
    },
}


def resolve_llm_provider() -> LLMProvider:
    """Resuelve el proveedor explícito o infiere el único que tiene clave.
    `LLM_PROVIDER` es obligatorio si hay dos claves configuradas; fallar de
    forma explícita evita que un cambio en `.env` altere el proveedor sin aviso.
    """
    configured_provider = (os.getenv("LLM_PROVIDER") or "").strip().casefold()
    groq_key = (GROQ_API_KEY or "").strip()
    openai_key = (OPENAI_API_KEY or "").strip()

    if configured_provider == "groq":
        if not groq_key:
            raise RuntimeError("LLM_PROVIDER=groq requiere GROQ_API_KEY.")
        return "groq"

    if configured_provider == "openai":
        if not openai_key:
            raise RuntimeError("LLM_PROVIDER=openai requiere OPENAI_API_KEY.")
        return "openai"

    if configured_provider:
        raise ValueError(
            "LLM_PROVIDER debe ser 'groq' u 'openai'; "
            f"se recibió {configured_provider!r}."
        )

    # Solo se infiere automáticamente cuando exactamente una clave está activa.
    enabled_providers = [
        provider
        for provider, api_key in (
            ("groq", groq_key),
            ("openai", openai_key),
        )
        if api_key
    ]

    if len(enabled_providers) == 1:
        return enabled_providers[0]

    if len(enabled_providers) == 2:
        raise RuntimeError(
            "Hay claves de Groq y OpenAI configuradas. "
            "Elegí una con LLM_PROVIDER=groq u LLM_PROVIDER=openai."
        )

    raise RuntimeError(
        "No hay un proveedor de lenguaje configurado. "
        "Configurá GROQ_API_KEY u OPENAI_API_KEY."
    )


def create_chat_model(
    purpose: LLMPurpose = "general",
    *,
    temperature: float = 0,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Construye el chat model para el uso general o la identidad de una obra.
    Resuelve primero proveedor y clave; luego aplica el modelo específico si
    existe una variable de entorno y, de lo contrario, usa el valor por defecto.
    `max_tokens` se omite si no se pidió para conservar el comportamiento propio
    del proveedor.
    """
    provider = resolve_llm_provider()
    configured_api_key = GROQ_API_KEY if provider == "groq" else OPENAI_API_KEY
    if not configured_api_key or not configured_api_key.strip():
        raise RuntimeError(f"No se encontró la clave configurada para {provider}.")

    api_key = configured_api_key.strip()
    model_env_var = MODEL_ENV_VARS[provider][purpose]
    model = (
        (os.getenv(model_env_var) or "").strip()
        or DEFAULT_MODELS[provider][purpose]
    )
    # Mantener una sola estructura de opciones evita duplicar configuración común y limita las diferencias a la clase concreta de cada proveedor.
    options: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "api_key": api_key,
    }

    if max_tokens is not None:
        options["max_tokens"] = max_tokens

    if provider == "groq":
        return ChatGroq(**options)
    return ChatOpenAI(**options)


def rate_limit_wait_seconds(exc: Exception, attempt: int) -> float:
    """Calcula la pausa antes del siguiente intento.
    Prioriza los tiempos publicados por la API (cabeceras o mensaje) y recurre
    a backoff exponencial si no hay una indicación legible. La espera se acota
    para evitar pausas demasiado cortas o prolongadas.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    # Los proveedores expresan la espera en milisegundos o segundos, y pueden usar una cabecera de reinicio de cuota en lugar de `Retry-After`.
    retry_after = (
        headers.get("retry-after-ms")
        or headers.get("retry-after")
        or headers.get("x-ratelimit-reset-tokens")
    )

    if retry_after:
        match = re.fullmatch(r"\s*([\d.]+)\s*(ms|s)?\s*", str(retry_after), re.I)
        if match:
            delay = float(match.group(1))
            if (match.group(2) or "s").casefold() == "ms":
                delay /= 1000
            return min(max(delay, 0.1), 10.0) + 0.05

    # Algunas respuestas incluyen el tiempo sugerido solo en el texto del error.
    message_delay = re.search(
        r"try again in\s+([\d.]+)\s*(ms|s)\b",
        str(exc),
        re.I,
    )
    if message_delay:
        delay = float(message_delay.group(1))
        if message_delay.group(2).casefold() == "ms":
            delay /= 1000
        return min(max(delay, 0.1), 10.0) + 0.05

    # Fallback acotado: aumenta con cada intento sin exceder cuatro segundos.
    return min(0.5 * (2 ** attempt), 4.0)


def invoke_with_rate_limit_retry(
    runnable: Any,
    prompt: str,
    operation: str,
) -> Any:
    """Invoca un runnable y reintenta solo excepciones de rate limit conocidas.
    Los demás errores se propagan inmediatamente. Al agotarse los intentos,
    también se vuelve a lanzar la última excepción para que el llamador la maneje.
    """
    max_attempts = 4
    # `attempt` empieza en cero para calcular el backoff y termina tras cuatro invocaciones como máximo, incluida la original.
    for attempt in range(max_attempts):
        try:
            return runnable.invoke(prompt)
        except RATE_LIMIT_ERRORS as exc:
            if attempt == max_attempts - 1:
                raise

            delay = rate_limit_wait_seconds(exc, attempt)
            provider = (
                "Groq"
                if isinstance(exc, GroqRateLimitError)
                else "OpenAI"
            )
            print(
                f"Límite temporal de {provider} al {operation}; "
                f"reintento {attempt + 1}/{max_attempts - 1} "
                f"en {delay:.2f}s."
            )
            time.sleep(delay)
