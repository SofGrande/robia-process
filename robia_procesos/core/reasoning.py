"""Sintetizador de reasoning natural para la col H del Sheet.

Toma los CriterioEvaluado de un proceso (Id Usuario/Org, Estado da Conversa,
etc.) y produce **una explicación conversacional de 1-2 oraciones** en lugar
del enumerado técnico por sub-regla.

Idioma: español por defecto, portugués brasileño cuando ``pais == 'BR'``.

Usa GPT-4o con ``temperature=0`` para que el output sea estable y conciso.
"""
from __future__ import annotations

from robia_procesos.core import llm
from robia_procesos.core.contrato import CriterioEvaluado, Resultado

_ETIQUETA_ESTADO = {
    Resultado.THUMBS_UP: "[OK]",
    Resultado.THUMBS_DOWN: "[ERROR]",
    Resultado.NO_EVALUABLE: "[N/A]",
}


def _construir_system_prompt(idioma: str) -> str:
    return (
        "Sos un auditor experto de calidad CX en Tiendanube/Nuvemshop. Recibís "
        "el resultado de un evaluador automático sobre un proceso de un ticket "
        "de soporte, separado en sub-reglas (OK = correcto, ERROR = mal aplicado, "
        "N/A = no aplicaba al caso).\n\n"
        "Tu tarea: explicar el resultado en **1-2 oraciones**, en "
        f"**{idioma}**, de forma natural y conversacional. NO enumeres las "
        "sub-reglas. NO uses bullets, listas, ni emojis. Producí UN insight "
        "claro y útil para la auditora humana.\n\n"
        "Lineamientos según el caso:\n"
        "- Si TODAS las sub-reglas son N/A → explicá brevemente por qué el guru "
        "no tuvo que actuar en este proceso (ej. 'El cliente ya tenía la "
        "organización asociada desde el inicio, no requirió acción manual del guru').\n"
        "- Si hay UNO O VARIOS ERRORES → enfocate en qué faltó o falló. Si hay "
        "varios errores, combinalos en una sola oración natural. NO menciones "
        "lo que está OK o N/A.\n"
        "- Si todo está OK o es mezcla OK/N/A → describí brevemente lo que el "
        "guru hizo bien.\n\n"
        "Tono: directo, sin jerga técnica interna. Hablá de 'el guru' / 'o guru', "
        "'el cliente' / 'o cliente', 'el ticket' / 'o ticket'. NO uses palabras "
        "como 'sub-regla', 'thumbs_up', 'evaluable', 'NO_EVALUABLE'. NO incluyas "
        "comillas alrededor de la respuesta. Devolvé solo la oración final."
    )


def sintetizar(
    criterios: list[CriterioEvaluado],
    criterio_label: str,
    pais: str = "AR",
) -> str:
    """Sintetiza un reasoning natural a partir de los CriterioEvaluado.

    Args:
        criterios: sub-reglas evaluadas del proceso (típicamente 3-5).
        criterio_label: nombre del proceso (ej. 'Id Usuario/Org').
        pais: AR/LT → español; BR → portugués brasileño.

    Returns:
        Texto natural de 1-2 oraciones. Si falla el LLM, fallback al
        enumerado clásico para no perder información.
    """
    if not criterios:
        return ""

    idioma = "portugués brasileño" if pais.upper() == "BR" else "español"

    detalles = []
    for c in criterios:
        etiqueta = _ETIQUETA_ESTADO.get(c.resultado, "[?]")
        detalles.append(f"- {etiqueta} {c.regla}")
    detalles_str = "\n".join(detalles)

    user = (
        f"Proceso evaluado: {criterio_label}\n\n"
        f"Resultado de las sub-reglas:\n{detalles_str}\n\n"
        "Reasoning natural:"
    )

    try:
        return llm.chat(
            user=user,
            system=_construir_system_prompt(idioma),
            temperature=0,
            max_tokens=200,
        ).strip()
    except Exception:
        # Fallback: enumerado simple sin LLM
        return _fallback_enumerado(criterios)


def _fallback_enumerado(criterios: list[CriterioEvaluado]) -> str:
    """Si falla el LLM, devolvemos el formato enumerado de antes."""
    EMOJI = {
        Resultado.THUMBS_UP: "✅",
        Resultado.THUMBS_DOWN: "❌",
        Resultado.NO_EVALUABLE: "⚪",
    }
    return "\n".join(f"{EMOJI[c.resultado]} {c.regla}" for c in criterios)
