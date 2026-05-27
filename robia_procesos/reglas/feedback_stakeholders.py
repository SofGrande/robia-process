"""IQS — Feedback → Stakeholders (lake + Zendesk API custom fields).

Cuando hay side conversation **activa al cierre del ticket**, el guru debe
haber completado los 2 campos de Stakeholder en el formulario de Zendesk:

- **Evaluación Stakeholders** (custom_field_id=``19759793671444``)
- **Feedback Stakeholders** (custom_field_id=``4713483575060``)

Ambos deben tener valor completo (distinto de ``NULL`` y ``'-'``). Si uno está
completo y el otro no, hay oportunidad de mejora **en ese tipo en particular**
(regla canónica Sofía 2026-05-13).

Por eso este módulo emite **2 sub-reglas distintas** (una por cada campo),
para que el reporte sea granular y el guru sepa qué falta exactamente.

Pipeline:

1. Detectar si el ticket tiene SD activa al cierre — alguna SD con
   ``sd_status != 'closed'``. Si no hay SD o todas están closed → ambas
   sub-reglas emiten ``NO_EVALUABLE``.
2. Si hay SD activa → fetch los 2 campos via Zendesk API.
3. Para cada campo: ``THUMBS_UP`` si completo, ``THUMBS_DOWN`` si vacío.
"""
from __future__ import annotations

from typing import Iterable

from robia_procesos.core import db, zendesk_api
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)

CRITERIO = "Feedback - Stakeholders"
SUB_REGLA_EVALUACION = "feedback_stakeholders_evaluacion_completa"
SUB_REGLA_FEEDBACK = "feedback_stakeholders_feedback_completo"

# IDs confirmados por Sofía (2026-05-13).
FIELD_ID_EVALUACION = 19759793671444   # "Evaluación Stakeholders"
FIELD_ID_FEEDBACK = 4713483575060      # "Feedback Stakeholders"

TABLA_SD = "g__general__side_conversations__agg_ticket"

# Valores que el formulario de Zendesk usa como placeholder "vacío".
# Tanto NULL/None como '-' significan que el guru no completó el campo.
_VALORES_VACIOS = frozenset({"-", "", "null", "none"})


def _valor_completo(v: object) -> bool:
    """True si el valor del custom field está completo (no vacío/placeholder)."""
    if v is None or v is False:
        return False
    s = str(v).strip()
    if not s:
        return False
    return s.lower() not in _VALORES_VACIOS


def _sds_del_ticket(ticket_id: int) -> list[tuple[int, str, str]]:
    """Devuelve [(sd_id, sd_status, sd_subject), ...] del ticket.

    Recordar Lección 1 de calibración: filtrar por ``sd_parent_ticket_id``
    (string), NO por ``sd_ticket_id``.
    """
    rows = db.fetch(
        f"""
        SELECT sd_ticket_id, sd_status, sd_subject
        FROM {db.FQN}.`{TABLA_SD}`
        WHERE CAST(sd_parent_ticket_id AS BIGINT) = {int(ticket_id)}
        """
    )
    return [(int(r[0]) if r[0] is not None else 0, r[1] or "", r[2] or "") for r in rows]


def _tiene_sd_activa(sds: list[tuple[int, str, str]]) -> bool:
    """¿Alguna SD del ticket sigue activa (sd_status != 'closed')?"""
    return any((status or "").strip().lower() != "closed" for _id, status, _subj in sds)


def _custom_fields_stakeholder(ticket_id: int) -> dict[str, object]:
    """Devuelve {'evaluacion': valor, 'feedback': valor} via Zendesk API."""
    ticket = zendesk_api.get_ticket(ticket_id)
    out: dict[str, object] = {"evaluacion": None, "feedback": None}
    for cf in ticket.get("custom_fields", []) or []:
        cid = cf.get("id")
        if cid == FIELD_ID_EVALUACION:
            out["evaluacion"] = cf.get("value")
        elif cid == FIELD_ID_FEEDBACK:
            out["feedback"] = cf.get("value")
    return out


_REGLA_EVALUACION = (
    "Si hay side conversation activa al cierre del ticket, el campo "
    "'Evaluación Stakeholders' (Zendesk custom field 19759793671444) debe "
    "estar completo. Califica la evaluación del guru sobre el equipo contactado."
)
_REGLA_FEEDBACK = (
    "Si hay side conversation activa al cierre del ticket, el campo "
    "'Feedback Stakeholders' (Zendesk custom field 4713483575060) debe "
    "estar completo. Registra el feedback narrativo sobre la interacción."
)


def _no_evaluable(
    ticket_id: int, sub_regla: str, regla: str, nota: str, evidencia: tuple[Evidencia, ...]
) -> CriterioEvaluado:
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.NO_EVALUABLE,
        regla=regla,
        confianza=Confianza.DIRECTA,
        evidencia=evidencia,
        nota=nota,
    )


def _veredicto_campo(
    ticket_id: int,
    sub_regla: str,
    regla: str,
    field_label: str,
    field_value: object,
    sds: list[tuple[int, str, str]],
) -> CriterioEvaluado:
    """Emite THUMBS_UP / THUMBS_DOWN según el valor del campo."""
    es_completo = _valor_completo(field_value)
    resultado = Resultado.THUMBS_UP if es_completo else Resultado.THUMBS_DOWN
    sd_summary = " | ".join(
        f"sd={sd_id} status={status} subject={subj[:40]}"
        for sd_id, status, subj in sds
    )
    valor_repr = repr(field_value) if field_value not in (None, "") else "(vacío)"
    nota = (
        f"{field_label} = {valor_repr}. "
        + ("Completo." if es_completo else "Vacío — el guru no completó el campo.")
    )
    evidencia = (
        Evidencia(
            tabla=TABLA_SD,
            descripcion=f"Side conversation(s) del ticket ({len(sds)})",
            valor=sd_summary or "(ninguna)",
        ),
        Evidencia(
            tabla="Zendesk API custom_fields",
            descripcion=field_label,
            valor=valor_repr,
        ),
    )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=resultado,
        regla=regla,
        confianza=Confianza.DIRECTA,
        evidencia=evidencia,
        nota=nota,
    )


def evaluar(ticket_ids: Iterable[int]) -> list[CriterioEvaluado]:
    resultados: list[CriterioEvaluado] = []
    for tid in ticket_ids:
        sds = _sds_del_ticket(tid)
        sd_activa = _tiene_sd_activa(sds)

        if not sd_activa:
            sd_summary = (
                f"Tiene {len(sds)} SD(s), todas closed — el feedback ya no se exige."
                if sds
                else "El ticket no tiene side conversations."
            )
            evidencia = (
                Evidencia(
                    tabla=TABLA_SD,
                    descripcion="Estado de side conversations al cierre",
                    valor=sd_summary,
                ),
            )
            resultados.append(
                _no_evaluable(
                    tid, SUB_REGLA_EVALUACION, _REGLA_EVALUACION, sd_summary, evidencia
                )
            )
            resultados.append(
                _no_evaluable(
                    tid, SUB_REGLA_FEEDBACK, _REGLA_FEEDBACK, sd_summary, evidencia
                )
            )
            continue

        try:
            cf = _custom_fields_stakeholder(tid)
        except Exception as e:
            err_nota = f"Falló get_ticket: {type(e).__name__}: {e}"
            resultados.append(
                _no_evaluable(
                    tid, SUB_REGLA_EVALUACION, _REGLA_EVALUACION, err_nota, ()
                )
            )
            resultados.append(
                _no_evaluable(
                    tid, SUB_REGLA_FEEDBACK, _REGLA_FEEDBACK, err_nota, ()
                )
            )
            continue

        resultados.append(
            _veredicto_campo(
                tid,
                SUB_REGLA_EVALUACION,
                _REGLA_EVALUACION,
                "Evaluación Stakeholders",
                cf["evaluacion"],
                sds,
            )
        )
        resultados.append(
            _veredicto_campo(
                tid,
                SUB_REGLA_FEEDBACK,
                _REGLA_FEEDBACK,
                "Feedback Stakeholders",
                cf["feedback"],
                sds,
            )
        )

    return resultados
