"""IQS — Procesos Zendesk → Estado de la conversación.

Referencia: IQS Guideline (Crítico para el Negocio → Procesos Zendesk → Estado).
La guía pide que el status del ticket se aplique según la 'Jornada Guru':

- Pendiente: el guru necesita información del merchant.
- En espera (hold): el guru espera resolución interna (TS, finanzas, side conv).
- Resuelto/Snoozed: la respuesta está completa, no hay más por responder.

Las sub-reglas que codifica este módulo:

- ``cierre_coherente``  → la timeline de status pasa por ``solved`` antes de
  ``closed``. Si un ticket aparece ``closed`` sin haber pasado por ``solved``
  es una anomalía operativa (cierre forzado, bug ETL, o flujo no estándar).
  Si el ticket sigue abierto, devuelve ``no_evaluable``.

- ``pending_post_solved_sin_trigger`` → detecta el anti-patrón "uso de
  Pendiente cuando la respuesta debería marcarse Resuelto/Snoozed":
  el ticket ya estuvo en ``solved`` y luego volvió a ``pending`` sin que
  haya una interacción entrante del merchant entre medio. Cruza
  ``tickets_events`` (status) con ``zendesk_interactions__event``
  (``interaction_type = 'in'``).

- ``hold_sin_side_conversation`` → status ``hold`` (En espera) sostenido
  por más de ``THRESHOLD_HOLD_HORAS`` (default 24h) sin que exista una
  side conversation asociada. La guideline dice que "En espera" se usa
  cuando el guru espera resolución interna (TS, Finanzas, side conv); si
  no hay ninguna pista de eso, queda como uso incorrecto de En espera.

Sub-reglas planificadas (Fase 4):

- ``ciclo_pending_oscilante`` → más de 3 ciclos ``open ↔ pending`` en menos de
  30 min sin trigger del merchant.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from robia_procesos.core import db
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)

CRITERIO = "Procesos Zendesk - Estado de la conversación"
TABLA_STATUS = "s__general__zendesk_tickets_events__event"
TABLA_INTERACTIONS = "s__general__zendesk_interactions__event"
TABLA_SIDE_CONV = "g__general__side_conversations__agg_ticket"
# Columna de timestamp en zendesk_interactions__event. Confirmada vía smoke
# test contra el lake real. Otras columnas útiles disponibles si más adelante
# queremos refinar la detección del actor: ``author_id``, ``author_type``.
COL_TS_INTERACTION = "interaction_timestamp"

# Umbral por encima del cual un tramo de 'hold' sin side conversation se
# considera anómalo. La guideline no especifica número; 24h cubre el caso
# típico "guru lo dejó en hold y se olvidó".
THRESHOLD_HOLD_HORAS = 24


def _to_datetime(v: object) -> datetime | None:
    """Acepta datetime, ``'YYYY-MM-DD HH:MM:SS'`` o ISO 8601. None si no parseable."""
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("T", " ").split(".")[0])
        except ValueError:
            return None
    return None


def _cargar_timeline(ticket_ids: Iterable[int]) -> dict[int, list[tuple[datetime, str]]]:
    """Devuelve, por ticket, la línea de tiempo (timestamp, status) ordenada."""
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    in_clause = ", ".join(str(t) for t in ids)
    rows = db.fetch(
        f"""
        SELECT ticket_id, event_timestamp, field_value
        FROM {db.FQN}.`{TABLA_STATUS}`
        WHERE ticket_id IN ({in_clause}) AND lower(field_name) = 'status'
        ORDER BY ticket_id, event_timestamp
        """
    )
    timeline: dict[int, list[tuple[datetime, str]]] = defaultdict(list)
    for ticket_id, ts, value in rows:
        ts_dt = _to_datetime(ts)
        if ts_dt is None:
            continue
        timeline[int(ticket_id)].append((ts_dt, str(value).lower()))
    return timeline


def _evaluar_cierre_coherente(
    ticket_id: int, eventos: list[tuple[datetime, str]]
) -> CriterioEvaluado:
    sub_regla = "cierre_coherente"
    regla = (
        "La línea de tiempo de status debe terminar en 'solved' antes de 'closed'. "
        "Un cierre directo sin pasar por 'solved' es uso incorrecto del estado."
    )
    if not eventos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.DIRECTA,
            nota="Sin eventos de status en el lake para este ticket.",
        )

    estados = [s for _, s in eventos]
    final = estados[-1]

    if final != "closed":
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.DIRECTA,
            evidencia=(
                Evidencia(
                    tabla=TABLA_STATUS,
                    descripcion="Estado actual",
                    timestamp=eventos[-1][0],
                    valor=final,
                ),
            ),
            nota="Ticket aún no cerrado; el cierre coherente solo se evalúa al final del ciclo.",
        )

    paso_por_solved = "solved" in estados[:-1]
    ts_close = eventos[-1][0]
    evidencia = (
        Evidencia(
            tabla=TABLA_STATUS,
            descripcion="Cierre",
            timestamp=ts_close,
            valor="closed",
        ),
    )
    if paso_por_solved:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.DIRECTA,
            evidencia=evidencia,
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_DOWN,
        regla=regla,
        confianza=Confianza.DIRECTA,
        evidencia=evidencia,
        nota=(
            "Ticket cerrado sin haber pasado por 'solved'. "
            f"Secuencia observada: {' -> '.join(estados)}"
        ),
    )


def _cargar_interacciones_in(
    ticket_ids: Iterable[int],
) -> dict[int, list[datetime]]:
    """Devuelve, por ticket, los timestamps de interacciones entrantes (merchant)."""
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    in_clause = ", ".join(str(t) for t in ids)
    rows = db.fetch(
        f"""
        SELECT ticket_id, {COL_TS_INTERACTION}
        FROM {db.FQN}.`{TABLA_INTERACTIONS}`
        WHERE ticket_id IN ({in_clause}) AND interaction_type = 'in'
        ORDER BY ticket_id, {COL_TS_INTERACTION}
        """
    )
    inter: dict[int, list[datetime]] = defaultdict(list)
    for ticket_id, ts in rows:
        ts_dt = _to_datetime(ts)
        if ts_dt is None:
            continue
        inter[int(ticket_id)].append(ts_dt)
    return inter


def _evaluar_pending_post_solved(
    ticket_id: int,
    eventos: list[tuple[datetime, str]],
    interacciones_in: list[datetime],
) -> CriterioEvaluado:
    sub_regla = "pending_post_solved_sin_trigger"
    regla = (
        "Si el ticket pasó por 'solved' y vuelve a 'pending' sin que el merchant "
        "haya enviado una nueva interacción (entrante) entre medio, es uso "
        "incorrecto de Pendiente: la respuesta debía marcarse Resuelto/Snoozed."
    )
    if not eventos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Sin eventos de status en el lake.",
        )

    transiciones_problematicas: list[tuple[datetime, datetime]] = []
    last_solved_ts: datetime | None = None
    for ts, status in eventos:
        if status == "solved":
            last_solved_ts = ts
        elif status == "pending" and last_solved_ts is not None:
            hubo_trigger = any(last_solved_ts < ts_in < ts for ts_in in interacciones_in)
            if not hubo_trigger:
                transiciones_problematicas.append((last_solved_ts, ts))
            last_solved_ts = None  # reset hasta el próximo solved

    if not transiciones_problematicas:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="No hay transiciones solved → pending o todas tuvieron trigger del merchant.",
        )

    evidencia = tuple(
        Evidencia(
            tabla=TABLA_STATUS,
            descripcion="Transición solved → pending sin interacción entrante entre medio",
            timestamp=ts_pending,
            valor=f"solved@{ts_solved.isoformat()} → pending@{ts_pending.isoformat()}",
        )
        for ts_solved, ts_pending in transiciones_problematicas
    )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_DOWN,
        regla=regla,
        confianza=Confianza.PARCIAL,
        evidencia=evidencia,
        nota=(
            f"{len(transiciones_problematicas)} transición(es) solved → pending sin "
            "interacción entrante del merchant entre medio."
        ),
    )


def _cargar_tickets_con_side_conv(ticket_ids: Iterable[int]) -> tuple[set[int], str | None]:
    """Devuelve (set de ticket_ids con al menos una side conv abierta desde
    ellos, error_o_None).

    Nota crítica sobre la vista ``g__general__side_conversations__agg_ticket``:

    - ``sd_ticket_id`` es el ID de la **side conversation** (un ticket nuevo
      que crea Zendesk). NO es el ticket original.
    - ``sd_parent_ticket_id`` es el ticket **padre**, el que tenemos en la
      muestra. Es **string** en el lake.

    Por eso filtramos por ``sd_parent_ticket_id`` (cast a string).
    """
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return set(), None
    in_clause = ", ".join(f"'{t}'" for t in ids)
    try:
        rows = db.fetch(
            f"""
            SELECT DISTINCT sd_parent_ticket_id
            FROM {db.FQN}.`{TABLA_SIDE_CONV}`
            WHERE sd_parent_ticket_id IN ({in_clause})
            """
        )
    except Exception as e:  # noqa: BLE001
        return set(), str(e)
    out: set[int] = set()
    for r in rows:
        if r and r[0] is not None:
            try:
                out.add(int(r[0]))
            except (ValueError, TypeError):
                continue
    return out, None


def _detectar_tramos_hold(
    eventos: list[tuple[datetime, str]],
) -> list[tuple[datetime, datetime | None, float | None]]:
    """Devuelve la lista de tramos ``(inicio, fin_o_None, horas_o_None)`` de
    status 'hold'. Si el ticket sigue en hold al final, ``fin`` es None y
    ``horas`` es None (no calculable sin saber "ahora")."""
    tramos: list[tuple[datetime, datetime | None, float | None]] = []
    inicio: datetime | None = None
    for ts, status in eventos:
        if status == "hold":
            if inicio is None:
                inicio = ts
        else:
            if inicio is not None:
                horas = (ts - inicio).total_seconds() / 3600
                tramos.append((inicio, ts, horas))
                inicio = None
    if inicio is not None:
        tramos.append((inicio, None, None))
    return tramos


def _evaluar_hold_sin_side_conv(
    ticket_id: int,
    eventos: list[tuple[datetime, str]],
    tickets_con_side_conv: set[int],
    error_side_conv: str | None,
) -> CriterioEvaluado:
    sub_regla = "hold_sin_side_conversation"
    regla = (
        f"Si el ticket estuvo en 'hold' (En espera) por más de "
        f"{THRESHOLD_HOLD_HORAS}h sin tener side conversation asociada, "
        "es uso incorrecto de En espera (debería implicar resolución interna "
        "como TS/Finanzas/side conv)."
    )
    if not eventos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Sin eventos de status para este ticket.",
        )
    if error_side_conv:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota=f"No se pudo consultar {TABLA_SIDE_CONV}: {error_side_conv[:120]}",
        )

    tramos = _detectar_tramos_hold(eventos)
    if not tramos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Ticket no estuvo en hold en ningún momento.",
        )

    largos = [(i, f, h) for i, f, h in tramos if h is not None and h > THRESHOLD_HOLD_HORAS]
    abierto = any(h is None for _, _, h in tramos)  # tramo en curso
    if not largos and not abierto:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota=(
                f"Hubo {len(tramos)} tramos de hold pero ninguno superó "
                f"{THRESHOLD_HOLD_HORAS}h."
            ),
        )

    if ticket_id in tickets_con_side_conv:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota=(
                f"{len(largos)} tramo(s) de hold > {THRESHOLD_HOLD_HORAS}h, "
                "pero el ticket tiene side conversation asociada."
            ),
        )

    evidencia = tuple(
        Evidencia(
            tabla=TABLA_STATUS,
            descripcion=f"Tramo en 'hold' de {h:.1f}h",
            timestamp=i,
            valor=f"{i.isoformat()} → {f.isoformat()}",
        )
        for i, f, h in largos
        if h is not None and f is not None
    )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_DOWN,
        regla=regla,
        confianza=Confianza.PARCIAL,
        evidencia=evidencia,
        nota=(
            f"{len(largos)} tramo(s) de hold > {THRESHOLD_HOLD_HORAS}h sin side "
            "conversation asociada en el lake; revisar si fue derivación interna "
            "u olvido del guru."
        ),
    )


def evaluar(ticket_ids: list[int]) -> list[CriterioEvaluado]:
    """Punto de entrada de la regla. Devuelve un CriterioEvaluado por ticket × sub_regla."""
    timeline = _cargar_timeline(ticket_ids)
    interacciones = _cargar_interacciones_in(ticket_ids)
    tickets_con_side_conv, err_sc = _cargar_tickets_con_side_conv(ticket_ids)
    out: list[CriterioEvaluado] = []
    for tid in ticket_ids:
        eventos = timeline.get(tid, [])
        out.append(_evaluar_cierre_coherente(tid, eventos))
        out.append(_evaluar_pending_post_solved(tid, eventos, interacciones.get(tid, [])))
        out.append(
            _evaluar_hold_sin_side_conv(tid, eventos, tickets_con_side_conv, err_sc)
        )
    return out
