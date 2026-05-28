"""IQS — Estado da Conversa (wrapper liviano sobre código existente).

Re-emite los CriterioEvaluado de `estado_conversacion.evaluar()` con:
    - ``criterio = "Estado da Conversa"`` (formato del Sheet)
    - ``regla`` reescrita con etiquetas amigables tipo ``"Etiqueta: descripción"``

Sub-reglas integradas hoy:
    - cierre_coherente (4.4)
    - pending_post_solved_sin_trigger (4.1 parcial — heurística determinística)
    - hold_sin_side_conversation (4.2)

Pendiente (próxima sesión):
    - 4.1 LLM completo (estado_pending_llm.py — ya existe sin tests)
    - 4.3 hold sin macro Issue no triaged (sin código todavía)
"""
from __future__ import annotations

from robia_procesos.core.contrato import (
    CriterioEvaluado,
    Resultado,
)
from robia_procesos.reglas import estado_conversacion as _base

CRITERIO = "Estado da Conversa"

# Mapping (sub_regla, resultado) → texto amigable en formato "Etiqueta: descripción".
# Sin IDs largos ni nombres técnicos. Si una combinación no está mapeada, se
# usa el texto original como fallback (no rompe).
_TEXTOS_AMIGABLES: dict[tuple[str, Resultado], str] = {
    ("cierre_coherente", Resultado.THUMBS_UP):
        "Cierre: El ticket se cerró correctamente pasando por 'resuelto'.",
    ("cierre_coherente", Resultado.THUMBS_DOWN):
        "Cierre: El ticket se cerró directamente sin pasar por 'resuelto'.",
    ("cierre_coherente", Resultado.NO_EVALUABLE):
        "Cierre: El ticket todavía no se cerró.",

    ("pending_post_solved_sin_trigger", Resultado.THUMBS_UP):
        "Uso de 'Pendiente': Coherente — no se reutilizó 'pendiente' después de 'resuelto'.",
    ("pending_post_solved_sin_trigger", Resultado.THUMBS_DOWN):
        "Uso de 'Pendiente': El ticket volvió a 'pendiente' después de 'resuelto' sin nueva respuesta del cliente.",
    ("pending_post_solved_sin_trigger", Resultado.NO_EVALUABLE):
        "Uso de 'Pendiente': No aplica para este ticket.",

    ("hold_sin_side_conversation", Resultado.THUMBS_UP):
        "Uso de 'En espera': Aplicado correctamente con side conversation abierta.",
    ("hold_sin_side_conversation", Resultado.THUMBS_DOWN):
        "Uso de 'En espera': Ticket en espera por más de 24h sin side conversation abierta.",
    ("hold_sin_side_conversation", Resultado.NO_EVALUABLE):
        "Uso de 'En espera': No aplica (el ticket no estuvo en 'en espera').",
}


def _re_emitir(c: CriterioEvaluado) -> CriterioEvaluado:
    """Reemite un CriterioEvaluado con criterio corto + regla amigable."""
    nueva_regla = _TEXTOS_AMIGABLES.get((c.sub_regla, c.resultado), c.regla)
    return CriterioEvaluado(
        ticket_id=c.ticket_id,
        criterio=CRITERIO,
        sub_regla=c.sub_regla,
        resultado=c.resultado,
        regla=nueva_regla,
        confianza=c.confianza,
        evidencia=c.evidencia,
        nota=None,  # se omite la nota técnica original para no duplicar info
    )


def evaluar_estado_conversa(ticket_id: int) -> list[CriterioEvaluado]:
    """Corre las 3 sub-reglas existentes sobre 1 ticket y reemite amigables."""
    crudos = _base.evaluar([ticket_id])
    return [_re_emitir(c) for c in crudos]
