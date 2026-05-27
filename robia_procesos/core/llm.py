"""Cliente OpenAI — wrapper mínimo para las sub-reglas LLM.

Centraliza la lectura de la API key (acepta `OPENAI_API_KEY` y la variante
minúscula `open_ai_key` que vive en el `.env` local de Sofía), el modelo
default y la firma uniforme de las llamadas. Las reglas no instancian su
propio cliente: piden ``chat(...)`` y se desentienden del SDK.

Uso típico::

    from robia_procesos.core import llm
    veredicto = llm.chat(
        system="Sos un evaluador IQS. Respondés SOLO con JSON.",
        user="Clasificá esta interacción: ...",
        modelo="gpt-4o",
        temperature=0,
    )
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / "Credenciales" / ".env")

# gpt-4o es lo que usa robia-qa (Soft Skills) — mantenemos consistencia entre
# RobIAs salvo que medamos peor accuracy en clasificación, ahí evaluamos
# bajar a gpt-4o-mini para sub-reglas de menor complejidad.
MODELO_DEFAULT = "gpt-4o"


def _api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("open_ai_key")
    if not key:
        raise RuntimeError(
            "Falta OPENAI_API_KEY (o variante 'open_ai_key') en "
            "Credenciales/.env."
        )
    return key


@lru_cache(maxsize=1)
def cliente() -> OpenAI:
    """Cliente OpenAI singleton — reusa la misma conexión entre llamadas."""
    return OpenAI(api_key=_api_key())


def chat(
    user: str,
    system: str | None = None,
    modelo: str = MODELO_DEFAULT,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> str:
    """Llamada one-shot a Chat Completions.

    Args:
        user: prompt del usuario (el contenido a clasificar/evaluar).
        system: instrucción de rol/criterios. Opcional pero recomendado.
        modelo: id del modelo. Default ``gpt-4o``.
        temperature: 0 para tareas determinísticas (clasificación, JSON).
        max_tokens: corte de la respuesta. None = sin límite explícito.
        response_format: ``{"type": "json_object"}`` para forzar JSON válido.

    Returns:
        Texto de la respuesta del assistant.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    kwargs: dict[str, object] = {
        "model": modelo,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format

    resp = cliente().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""
