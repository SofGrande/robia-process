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

import json
from html import unescape

from robia_procesos.core import db, llm, zendesk_api as zd
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


# ────────────────────── LLM: ¿equipo destino correcto? ──────────────────────


_SYSTEM_LLM_EQUIPO = """\
Sos un auditor de calidad CX en Tiendanube/Nuvemshop, plataforma de e-commerce para pequeños lojistas.

Tu tarea: dado el contenido de un ticket de soporte y el equipo al que fue derivado, decidir si el equipo es **apropiado** para resolver el caso o si el ticket debió ir a otro equipo.

Cómo razonar:
- El **nombre del equipo** suele indicar su especialidad: Pago Nube / PN → temas de pagos, contracargos, cuotas, payouts, KYC, validación de identidad, onboarding, riesgo; Online → tienda online, dominios, diseño, productos, configuración; Envío Nube → logística, etiquetas, transportadoras, devoluciones; Partners / Success → atención a partners (agencias, desarrolladores); Triagem / To Assign → ruteo inicial (NUNCA es destino final correcto); Riesgo y activación SMBs → onboarding de cuentas Pago Nube, KYC, compliance, validación de identidad.
- Si el contenido del ticket habla CLARAMENTE de un dominio incompatible con el equipo (ej. dudas de envíos en un equipo de Pagos), marcalo INCORRECTO con evidencia explícita.
- **REGLA FUERTE: si el contenido del ticket es vago, genérico, está incompleto o no podés determinar el tema con claridad, devolvé correcto=true.** No alucines un sugerido; el equipo asignado es válido por default.
- Si el equipo destino es Triagem o To Assign, marcá correcto=false (Triagem no es destino final).

Devolvé SIEMPRE un JSON estricto con esta forma:
{"correcto": true | false, "razon": "máx 30 palabras", "equipo_sugerido": "<nombre o null>"}

Sin texto extra fuera del JSON. Sin markdown."""


def _limpiar_html(s: str | None) -> str:
    if not s:
        return ""
    import re
    out = unescape(s)
    out = re.sub(r"<[^>]+>", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


_PATRONES_BOT_COMMENT = (
    "conversation with",       # mensaje sistema al crear ticket de chat
    "🚨",                       # alerta del bot de sentimiento
    "¡alerta gurú",
    "alerta guru",
    "a solicitação #",         # notif merge de Zendesk
)


def _es_comment_de_bot(body: str) -> bool:
    """Heurística para descartar comments del bot/sistema que no aportan contenido real."""
    if not body:
        return True
    low = body.lower().strip()[:200]
    return any(p in low for p in _PATRONES_BOT_COMMENT)


def _resumen_ticket_para_llm(ticket: dict, comments: list[dict]) -> str:
    """Texto compacto del ticket priorizando comments reales del cliente.

    Filtra mensajes de bot/sistema (alertas, notificaciones de merge, etc.).
    Si después de filtrar no queda nada, devuelve solo el subject (el LLM
    debería defaultear a correcto cuando el contenido es vago).
    """
    subject = (ticket.get("subject") or "").strip()
    desc = _limpiar_html(ticket.get("description") or "")

    # Primeros 2 comments REALES (no bot/sistema)
    comments_reales: list[str] = []
    for c in comments:
        body = _limpiar_html(c.get("plain_body") or c.get("body") or "")
        if _es_comment_de_bot(body):
            continue
        if not body or len(body) < 15:
            continue
        comments_reales.append(body[:600])
        if len(comments_reales) >= 2:
            break

    partes = [f"Asunto: {subject or '(vacío)'}"]
    if desc and not _es_comment_de_bot(desc):
        partes.append(f"Descripción: {desc[:600]}")
    if comments_reales:
        for i, c in enumerate(comments_reales, 1):
            partes.append(f"Mensaje {i} del cliente: {c}")
    else:
        partes.append("(Sin contenido específico del cliente detectado en el ticket.)")
    return "\n".join(partes)


def _evaluar_equipo_correcto(
    ticket_id: int,
    grupo_destino_id: int,
) -> dict | None:
    """Pide al LLM si el equipo destino es apropiado. Devuelve dict o None si falla."""
    try:
        ticket = zd.get_ticket(ticket_id)
        comments = zd.get_ticket_comments(ticket_id, per_page=10, sort_order="asc")
        equipo_nombre = zd.get_group_name(grupo_destino_id)
    except Exception:
        return None

    contenido = _resumen_ticket_para_llm(ticket, comments)
    user = (
        f"Equipo destino: {equipo_nombre} (id={grupo_destino_id})\n\n"
        f"Ticket:\n{contenido}\n\n"
        "Devolvé el JSON con tu evaluación:"
    )
    try:
        respuesta = llm.chat(
            user=user,
            system=_SYSTEM_LLM_EQUIPO,
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(respuesta)
        return {
            "correcto": bool(parsed.get("correcto", True)),
            "razon": (parsed.get("razon") or "").strip(),
            "equipo_sugerido": (parsed.get("equipo_sugerido") or None),
            "equipo_destino_nombre": equipo_nombre,
        }
    except Exception:
        return None


# ────────────────────── Entry point ──────────────────────


def _aplicar_veredicto_llm(
    sub_reglas: list[CriterioEvaluado],
    historial: list[dict],
    veredicto: dict | None,
) -> list[CriterioEvaluado]:
    """Si el LLM dice equipo destino incorrecto, marca como THUMBS_DOWN la
    sub-regla del último actor que derivó. El resto queda como está.
    """
    if not veredicto or veredicto.get("correcto") is not False:
        return sub_reglas  # equipo correcto o LLM falló → no tocamos nada

    if not historial:
        return sub_reglas

    # Último actor humano/automático que derivó
    ultimo = historial[-1]
    actor_a_sub_regla = {
        "Triagem": "triagem_actuo",
        "Guru": "guru_derivo",
        "ADA": "ada_actuo",
        # Trigger no tiene sub-regla propia, va a derivacion_aplicada
    }
    sub_regla_target = actor_a_sub_regla.get(ultimo["actor"], "derivacion_aplicada")

    razon = veredicto.get("razon", "")
    sugerido = veredicto.get("equipo_sugerido")
    equipo_nombre = veredicto.get("equipo_destino_nombre", "el equipo destino")
    nuevo_texto = (
        f"{ultimo['actor']} derivó a {equipo_nombre}, pero el equipo no es "
        f"el correcto para el contenido del ticket"
    )
    if sugerido:
        nuevo_texto += f" (sugerido: {sugerido})"
    if razon:
        nuevo_texto += f". {razon}"
    nuevo_texto += "."

    out = []
    for c in sub_reglas:
        if c.sub_regla == sub_regla_target and c.resultado == Resultado.THUMBS_UP:
            out.append(CriterioEvaluado(
                ticket_id=c.ticket_id,
                criterio=c.criterio,
                sub_regla=c.sub_regla,
                resultado=Resultado.THUMBS_DOWN,
                regla=nuevo_texto,
                confianza=Confianza.HEURISTICA,
                evidencia=c.evidencia,
            ))
        else:
            out.append(c)
    return out


def evaluar_derivacoes(ticket_id: int) -> list[CriterioEvaluado]:
    """Corre las 4 sub-reglas + LLM 'equipo correcto?' sobre 1 ticket."""
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

    # Sub-reglas determinísticas (B4.1)
    sub_reglas = [
        _sub_triagem(ticket_id, historial),
        _sub_guru(ticket_id, historial),
        _sub_ada(ticket_id, historial),
        _sub_hubo_derivacion(ticket_id, historial),
    ]

    # Evaluar con LLM si el último equipo destino es apropiado (B4.3)
    if historial:
        grupo_final = historial[-1]["grupo_destino"]
        if grupo_final and grupo_final not in TRIAGEM_GROUPS:
            # Solo evaluamos si el destino final NO es Triagem (no tiene sentido
            # auditar "¿está bien en Triagem?" — Triagem nunca es destino final).
            veredicto = _evaluar_equipo_correcto(ticket_id, grupo_final)
            sub_reglas = _aplicar_veredicto_llm(sub_reglas, historial, veredicto)

    return sub_reglas
