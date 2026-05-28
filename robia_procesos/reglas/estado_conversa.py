"""IQS — Estado da Conversa (wrapper sobre código existente + LLM + override macros externas).

Combina 4 sub-reglas:
    - cierre_coherente                       (4.4 — determinístico, en estado_conversacion.py)
    - pending_post_solved_sin_trigger        (heurística determinística, en estado_conversacion.py)
    - hold_sin_side_conversation             (4.2 — determinístico, en estado_conversacion.py)
        + override: si el guru aplicó alguna macro `Medir conversa con dlocal/Andreani`
          al ticket, el hold se considera justificado por side conversation
          EXTERNA y se reemite como THUMBS_UP (Sofía 2026-05-28).
    - pending_mantenido_con_respuesta_completa  (4.1 — LLM, en estado_pending_llm.py)

Pendiente próxima sesión: 4.3 (hold sin macro Issue no triaged).
"""
from __future__ import annotations

from robia_procesos.core import db
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)
from robia_procesos.reglas import estado_conversacion as _base
from robia_procesos.reglas import estado_pending_llm as _pending_llm

CRITERIO = "Estado da Conversa"

# Macros que indican que el guru abrió una side conversation EXTERNA
# (no dentro de Zendesk SD). Si alguna de éstas fue aplicada, un hold de
# >24h sin SD Zendesk sigue siendo válido. Confirmadas por Sofía 2026-05-28.
MACROS_SD_EXTERNA: dict[int, str] = {
    36455482333460: "Medir conversa con dlocal - Contracargo",
    36455358251028: "Medir conversa con dlocal - Retiros",
    42171957879188: "Medir conversa con dlocal - Facturas",
    36455743972244: "Medir conversa con Dlocal - MODO",
    36455574743444: "Medir conversa con dlocal - Cuenta",
    24125879401748: "Medir conversa con dlocal - Payins",
    36455391774100: "Medir conversa con dlocal - Reembolsos",
    36455625442964: "Medir conversa con dlocal - Otros",
    36509421102228: "Medir conversa con dlocal - Devolución de fees",
    36455456544020: "Medir conversa con dlocal - Saldos y Balances",
    35226808480020: "Medir conversa con Portal Andreani/HOP",
}

TABLA_MACROS = "s__general__zendesk_macros_usage__event"


def _macro_sd_externa_aplicada(ticket_id: int) -> str | None:
    """Devuelve el nombre de la primera macro externa aplicada, o None."""
    ids = ",".join(str(k) for k in MACROS_SD_EXTERNA)
    sql = (
        f"SELECT macro_id FROM {db.FQN}.`{TABLA_MACROS}` "
        f"WHERE ticket_id = {ticket_id} AND macro_id IN ({ids}) LIMIT 1"
    )
    try:
        with db.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            if row:
                return MACROS_SD_EXTERNA.get(int(row[0]))
    except Exception:
        return None
    return None


# (sub_regla, resultado) → texto amigable.
_TEXTOS_AMIGABLES: dict[tuple[str, Resultado], str] = {
    ("cierre_coherente", Resultado.THUMBS_UP):
        "Cierre: El ticket se cerró correctamente pasando por 'resuelto'.",
    ("cierre_coherente", Resultado.THUMBS_DOWN):
        "Cierre: El ticket se cerró directamente sin pasar por 'resuelto'.",
    ("cierre_coherente", Resultado.NO_EVALUABLE):
        "Cierre: El ticket todavía no se cerró.",

    ("pending_post_solved_sin_trigger", Resultado.THUMBS_UP):
        "Uso de 'Pendiente' post-resuelto: Coherente — no se reutilizó 'pendiente' después de 'resuelto'.",
    ("pending_post_solved_sin_trigger", Resultado.THUMBS_DOWN):
        "Uso de 'Pendiente' post-resuelto: El ticket volvió a 'pendiente' después de 'resuelto' sin nueva respuesta del cliente.",
    ("pending_post_solved_sin_trigger", Resultado.NO_EVALUABLE):
        "Uso de 'Pendiente' post-resuelto: No aplica para este ticket.",

    ("hold_sin_side_conversation", Resultado.THUMBS_UP):
        "Uso de 'En espera': Aplicado correctamente con side conversation abierta.",
    ("hold_sin_side_conversation", Resultado.THUMBS_DOWN):
        "Uso de 'En espera': Ticket en espera por más de 24h sin side conversation abierta.",
    ("hold_sin_side_conversation", Resultado.NO_EVALUABLE):
        "Uso de 'En espera': No aplica (el ticket no estuvo en 'en espera').",

    ("pending_mantenido_con_respuesta_completa", Resultado.THUMBS_UP):
        "Uso de 'Pendiente' con respuesta: Aplicado correctamente — había pregunta de sondeo o action item para el cliente.",
    ("pending_mantenido_con_respuesta_completa", Resultado.THUMBS_DOWN):
        "Uso de 'Pendiente' con respuesta: Mal aplicado — la respuesta del guru estaba completa, debió ser 'resuelto' o 'snooze'.",
    ("pending_mantenido_con_respuesta_completa", Resultado.NO_EVALUABLE):
        "Uso de 'Pendiente' con respuesta: No aplica para este ticket.",
}


def _re_emitir(c: CriterioEvaluado, regla_override: str | None = None) -> CriterioEvaluado:
    """Reemite un CriterioEvaluado con criterio corto + texto amigable."""
    if regla_override is not None:
        nueva_regla = regla_override
    else:
        nueva_regla = _TEXTOS_AMIGABLES.get((c.sub_regla, c.resultado), c.regla)
    return CriterioEvaluado(
        ticket_id=c.ticket_id,
        criterio=CRITERIO,
        sub_regla=c.sub_regla,
        resultado=c.resultado,
        regla=nueva_regla,
        confianza=c.confianza,
        evidencia=c.evidencia,
        nota=None,
    )


def evaluar_estado_conversa(ticket_id: int) -> list[CriterioEvaluado]:
    """Corre las 4 sub-reglas (3 determinísticas + 1 LLM) y reemite amigables."""
    crudos = _base.evaluar([ticket_id])
    try:
        llm_resultados = _pending_llm.evaluar([ticket_id])
    except Exception as e:
        # Si el LLM falla, emitimos NO_EVALUABLE en su lugar (sin romper la corrida).
        llm_resultados = [
            CriterioEvaluado(
                ticket_id=ticket_id,
                criterio=CRITERIO,
                sub_regla="pending_mantenido_con_respuesta_completa",
                resultado=Resultado.NO_EVALUABLE,
                regla=f"Uso de 'Pendiente' con respuesta: No se pudo evaluar — {type(e).__name__}.",
                confianza=Confianza.HEURISTICA,
            )
        ]

    # Override para hold con macro externa.
    macro_externa: str | None = None
    necesita_override = any(
        c.sub_regla == "hold_sin_side_conversation"
        and c.resultado == Resultado.THUMBS_DOWN
        for c in crudos
    )
    if necesita_override:
        macro_externa = _macro_sd_externa_aplicada(ticket_id)

    salida: list[CriterioEvaluado] = []
    for c in crudos:
        if (
            c.sub_regla == "hold_sin_side_conversation"
            and c.resultado == Resultado.THUMBS_DOWN
            and macro_externa
        ):
            # Override: el hold se justifica por SD externa.
            override = CriterioEvaluado(
                ticket_id=c.ticket_id,
                criterio=c.criterio,
                sub_regla=c.sub_regla,
                resultado=Resultado.THUMBS_UP,
                regla=(
                    f"Uso de 'En espera': Justificado por side conversation externa "
                    f"({macro_externa})."
                ),
                confianza=Confianza.PARCIAL,
            )
            salida.append(_re_emitir(override, regla_override=override.regla))
        else:
            salida.append(_re_emitir(c))

    # Sumar resultados del LLM (4.1).
    for c in llm_resultados:
        salida.append(_re_emitir(c))

    return salida
