"""IQS — Procesos Zendesk → Derivações (B4.1 determinística, sin LLM).

Implementa la **detección del actor de cada derivación** (cambio de group_id):
distingue ADA / Triagem / Guru / Trigger Zendesk cruzando 3 tablas del lake
(`tickets_events` + `macros_usage` + `assignment`), conforme al patrón
validado en discovery (ver memoria project_derivaciones_pattern).

Esta es la **fase determinística** (B4.1). Las 4 sub-reglas reportan QUIÉN
hizo cada derivación pero todavía no juzgan si el equipo destino fue
correcto para el contenido — eso requiere LLM y queda para B4.3.

Sub-reglas emitidas:
    - `triagem_actuo`  → ¿Triagem derivó el ticket?
    - `guru_derivo`    → ¿El guru asignado aplicó macro de derivación?
    - `ada_actuo`      → ¿ADA participó en la derivación?
    - `derivacion_aplicada` → ¿Hubo alguna derivación en el ticket?
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from robia_procesos.core import db
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)

CRITERIO = "Derivações"

# Constantes confirmadas en discovery (memoria reference_ids_zendesk).
ADA_ASSIGNEE_ID = 49018376478100
ADA_GROUP_ID = -1

TRIAGEM_GROUPS: dict[int, str] = {
    4416857078676: "[AR] Triagem",
    1900001463187: "[BR] Triagem",
    7203714749716: "[MX] To Assign",
}

# Ventana de tolerancia (segundos) para asociar una macro con un cambio de
# group_id. En las muestras de calibración, el evento de cambio y la macro
# coinciden al segundo, pero damos margen por posibles desfases del ETL.
VENTANA_MACRO_SEGUNDOS = 5


def _to_dt(v: Any) -> datetime | None:
    """Convierte string ISO o datetime a datetime. None si no se puede."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        # El lake devuelve strings como "2026-05-15 15:07:42".
        return datetime.fromisoformat(str(v).replace("Z", "+00:00").split("+")[0])
    except Exception:
        return None


# ────────────────────── Loaders del lake ──────────────────────


def _cargar_cambios_grupo(ticket_id: int) -> list[dict]:
    """Devuelve la lista ordenada de cambios de group_id del ticket."""
    sql = f"""
        SELECT event_timestamp, field_value
        FROM {db.FQN}.`s__general__zendesk_tickets_events__event`
        WHERE ticket_id = {ticket_id} AND field_name = 'group_id'
        ORDER BY event_timestamp
    """
    with db.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() or []
    out = []
    for r in rows:
        ts = _to_dt(r[0])
        if ts is None:
            continue
        try:
            grupo = int(r[1]) if r[1] is not None else None
        except (ValueError, TypeError):
            grupo = None
        out.append({"timestamp": ts, "grupo_destino": grupo})
    return out


def _cargar_assignments(ticket_id: int) -> list[dict]:
    """Devuelve los registros de assignment del ticket (incluye AI Agent)."""
    sql = f"""
        SELECT assignee_id, guru_name, group_id,
               assignment_start_time, assignment_end_time
        FROM {db.FQN}.`s__general__zendesk_assignment__event`
        WHERE ticket_id = {ticket_id}
        ORDER BY assignment_start_time
    """
    with db.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() or []
    out = []
    for r in rows:
        out.append({
            "assignee_id": r[0],
            "guru_name": r[1],
            "group_id": r[2],
            "start": _to_dt(r[3]),
            "end": _to_dt(r[4]),
        })
    return out


def _cargar_macros(ticket_id: int) -> list[dict]:
    """Macros aplicadas al ticket — para detectar derivaciones manuales."""
    sql = f"""
        SELECT created_at, author_id, macro_id
        FROM {db.FQN}.`s__general__zendesk_macros_usage__event`
        WHERE ticket_id = {ticket_id}
        ORDER BY created_at
    """
    with db.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall() or []
    out = []
    for r in rows:
        ts = _to_dt(r[0])
        if ts is None:
            continue
        out.append({
            "timestamp": ts,
            "author_id": r[1],
            "macro_id": r[2],
        })
    return out


# ────────────────────── Identificación del actor ──────────────────────


def _identificar_actor(
    ts_cambio: datetime,
    assignments: list[dict],
    macros: list[dict],
    assignees_humanos: set[int],
) -> tuple[str, str]:
    """Dado el timestamp de un cambio de group_id, identifica al actor.

    Orden de prioridad (la macro es señal MÁS FUERTE que AI Agent asignado,
    porque un Triagem humano puede aplicar macros mientras el ticket está
    todavía formalmente asignado a AI Agent — caso 7448501 confirmado):

        1. ¿Macro aplicada en ±VENTANA_MACRO_SEGUNDOS? → Guru o Triagem según author.
        2. ¿En ventana de AI Agent sin macro? → ADA.
        3. Resto → Trigger Zendesk.

    Returns:
        (actor, detalle) donde actor ∈ {'ADA', 'Triagem', 'Guru', 'Trigger'}.
    """
    tolerancia = timedelta(seconds=VENTANA_MACRO_SEGUNDOS)

    # 1) ¿Macro aplicada en ventana de tolerancia?
    for m in macros:
        if abs(m["timestamp"] - ts_cambio) > tolerancia:
            continue
        author_id = m["author_id"]
        if author_id in assignees_humanos:
            return (
                "Guru",
                f"macro {m['macro_id']} aplicada por un guru asignado al ticket",
            )
        return (
            "Triagem",
            f"macro {m['macro_id']} aplicada por usuario no-assignee (probable Triagem)",
        )

    # 2) Sin macro pero ¿AI Agent activo? → ADA
    for a in assignments:
        if a["assignee_id"] != ADA_ASSIGNEE_ID:
            continue
        if a["start"] and a["end"] and a["start"] <= ts_cambio <= a["end"]:
            return ("ADA", "ADA tenía el ticket asignado al momento del cambio, sin macro humana")

    # 3) Trigger Zendesk puro
    return ("Trigger", "cambio de group_id sin macro asociada (trigger automático)")


def _construir_historial(
    cambios: list[dict],
    assignments: list[dict],
    macros: list[dict],
) -> list[dict]:
    """Combina cambios + assignments + macros en una línea de tiempo enriquecida."""
    # Lista de assignees humanos del ticket (no incluye ADA).
    assignees_humanos = {
        a["assignee_id"]
        for a in assignments
        if a["assignee_id"] != ADA_ASSIGNEE_ID
    }

    historial = []
    for c in cambios:
        actor, detalle = _identificar_actor(
            c["timestamp"], assignments, macros, assignees_humanos
        )
        historial.append({
            "timestamp": c["timestamp"],
            "grupo_destino": c["grupo_destino"],
            "grupo_destino_label": TRIAGEM_GROUPS.get(
                c["grupo_destino"], f"grupo {c['grupo_destino']}"
            ),
            "actor": actor,
            "detalle": detalle,
        })
    return historial


# ────────────────────── Sub-reglas ──────────────────────


def _resumir_evento(h: dict) -> str:
    """Texto humano de un evento de derivación, p/usar en `regla`."""
    return (
        f"{h['actor']} derivó a {h['grupo_destino_label']} "
        f"el {h['timestamp'].strftime('%Y-%m-%d %H:%M')}"
    )


def _evidencias_de_historial(historial: list[dict]) -> tuple[Evidencia, ...]:
    return tuple(
        Evidencia(
            tabla="historial_derivaciones",
            descripcion=_resumir_evento(h),
            timestamp=h["timestamp"],
            valor=f"actor={h['actor']} destino={h['grupo_destino']}",
        )
        for h in historial
    )


def _sub_triagem(ticket_id: int, historial: list[dict]) -> CriterioEvaluado:
    eventos = [h for h in historial if h["actor"] == "Triagem"]
    if not eventos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="triagem_actuo",
            resultado=Resultado.NO_EVALUABLE,
            regla="Triagem: No intervino en este ticket.",
            confianza=Confianza.DIRECTA,
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="triagem_actuo",
        resultado=Resultado.THUMBS_UP,
        regla=(
            f"Triagem: Derivó el ticket a "
            f"{eventos[0]['grupo_destino_label']} con macro. "
            "(Validación de si el equipo destino fue correcto pendiente.)"
        ),
        confianza=Confianza.DIRECTA,
        evidencia=_evidencias_de_historial(eventos),
    )


def _sub_guru(ticket_id: int, historial: list[dict]) -> CriterioEvaluado:
    eventos = [h for h in historial if h["actor"] == "Guru"]
    if not eventos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="guru_derivo",
            resultado=Resultado.NO_EVALUABLE,
            regla="Guru: No aplicó macro de derivación en este ticket.",
            confianza=Confianza.DIRECTA,
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="guru_derivo",
        resultado=Resultado.THUMBS_UP,
        regla=(
            f"Guru: Aplicó macro de derivación a "
            f"{eventos[-1]['grupo_destino_label']}. "
            "(Validación de si el equipo destino fue correcto pendiente.)"
        ),
        confianza=Confianza.DIRECTA,
        evidencia=_evidencias_de_historial(eventos),
    )


def _sub_ada(ticket_id: int, historial: list[dict]) -> CriterioEvaluado:
    eventos = [h for h in historial if h["actor"] == "ADA"]
    if not eventos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="ada_actuo",
            resultado=Resultado.NO_EVALUABLE,
            regla="ADA: No participó en la derivación de este ticket.",
            confianza=Confianza.DIRECTA,
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="ada_actuo",
        resultado=Resultado.THUMBS_UP,
        regla=(
            f"ADA: Derivó automáticamente a "
            f"{eventos[-1]['grupo_destino_label']}. "
            "(Validación de si el equipo destino fue correcto pendiente.)"
        ),
        confianza=Confianza.DIRECTA,
        evidencia=_evidencias_de_historial(eventos),
    )


def _sub_hubo_derivacion(ticket_id: int, historial: list[dict]) -> CriterioEvaluado:
    if not historial:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="derivacion_aplicada",
            resultado=Resultado.NO_EVALUABLE,
            regla="Derivación: No hubo cambios de equipo en este ticket.",
            confianza=Confianza.DIRECTA,
        )
    actores = sorted({h["actor"] for h in historial})
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="derivacion_aplicada",
        resultado=Resultado.THUMBS_UP,
        regla=(
            f"Derivación: El ticket tuvo {len(historial)} cambio(s) de equipo. "
            f"Actores: {', '.join(actores)}."
        ),
        confianza=Confianza.DIRECTA,
        evidencia=_evidencias_de_historial(historial),
    )


# ────────────────────── Entry point ──────────────────────


def evaluar_derivacoes(ticket_id: int) -> list[CriterioEvaluado]:
    """Corre las 4 sub-reglas determinísticas de Derivações sobre 1 ticket."""
    try:
        cambios = _cargar_cambios_grupo(ticket_id)
        assignments = _cargar_assignments(ticket_id)
        macros = _cargar_macros(ticket_id)
    except Exception as e:
        return [
            CriterioEvaluado(
                ticket_id=ticket_id,
                criterio=CRITERIO,
                sub_regla=sr,
                resultado=Resultado.NO_EVALUABLE,
                regla=f"Derivação: No se pudo consultar el lake — {type(e).__name__}.",
                confianza=Confianza.HEURISTICA,
            )
            for sr in (
                "triagem_actuo",
                "guru_derivo",
                "ada_actuo",
                "derivacion_aplicada",
            )
        ]

    historial = _construir_historial(cambios, assignments, macros)

    return [
        _sub_triagem(ticket_id, historial),
        _sub_guru(ticket_id, historial),
        _sub_ada(ticket_id, historial),
        _sub_hubo_derivacion(ticket_id, historial),
    ]
