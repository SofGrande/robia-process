"""IQS — Clasificación de la conversación → Naturaleza de la conversa.

Referencia: IQS Guideline → "Naturaleza de la Conversa". Hoja maestra:
'[LATAM] Naturaleza da Conversa' (ES) y '[BR] Natureza da Conversa' (PT-BR).

Catálogo de naturalezas válidas (unión LATAM + BR, vía aliases):
``Duda Autoatención``, ``Duda Investigativa``, ``Request``, ``Issue``,
``Problem``, ``Downtime``.

El lake muestra valores híbridos (``Duda/Dúvida Auto``, ``Problem/Feedback``,
etc.); el catálogo (`topicos_catalogo`) los matchea con aliases ES↔PT.

Sub-reglas implementadas:

- ``naturaleza_completa`` → última fila de ``zendesk_ticket_nature__event``
  tiene ``general_nature`` no vacío.
- ``naturaleza_valida`` → el valor (post-aliases) está en el catálogo.
- ``multinaturaleza_todas_validas`` → si hay >1 naturaleza activa (mismo
  timestamp máximo), todas deben figurar en el catálogo. Si solo hay una →
  ``no_evaluable`` ("una sola naturaleza, regla no aplica").

Sub-reglas planificadas (Fase 4):

- ``naturaleza_combinacion_coherente`` → validar combinaciones específicas
  de la guideline (p.ej. *Issue + Request* es combinación válida explícita).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from robia_procesos.core import db
from robia_procesos.core import topicos_catalogo
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)


def _to_datetime(v: object) -> datetime | None:
    """Acepta datetime, ``'YYYY-MM-DD HH:MM:SS'`` o ISO 8601."""
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("T", " ").split(".")[0])
        except ValueError:
            return None
    return None

CRITERIO = "Clasificación de la conversación - Naturaleza"
TABLA = "s__general__zendesk_ticket_nature__event"


def _cargar_ultima_naturaleza(ticket_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    in_clause = ", ".join(str(t) for t in ids)
    rows = db.fetch(
        f"""
        WITH n AS (
          SELECT
            ticket_id,
            general_nature,
            row_number() OVER (
              PARTITION BY ticket_id
              ORDER BY coalesce(sys_audit_updated_on, sys_audit_created_on) DESC
            ) AS rw
          FROM {db.FQN}.`{TABLA}`
          WHERE ticket_id IN ({in_clause})
        )
        SELECT ticket_id, general_nature FROM n WHERE rw = 1
        """
    )
    return {int(r[0]): str(r[1] or "").strip() for r in rows}


def _evaluar_completitud(ticket_id: int, valor: str | None) -> CriterioEvaluado:
    sub_regla = "naturaleza_completa"
    regla = "Última fila de zendesk_ticket_nature__event debe tener general_nature no vacío."
    if valor is None:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.DIRECTA,
            nota="Sin filas en zendesk_ticket_nature__event para este ticket.",
        )
    if not valor:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_DOWN,
            regla=regla,
            confianza=Confianza.DIRECTA,
            nota="general_nature vacío en la última fila.",
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_UP,
        regla=regla,
        confianza=Confianza.DIRECTA,
        evidencia=(
            Evidencia(tabla=TABLA, descripcion="general_nature", valor=valor),
        ),
    )


def _evaluar_validez(
    ticket_id: int, valor: str | None, catalogo: topicos_catalogo.Catalogo
) -> CriterioEvaluado:
    sub_regla = "naturaleza_valida"
    regla = (
        "El valor de general_nature (con aliases ES↔PT) debe figurar en el "
        "catálogo de Naturaleza (LATAM/BR)."
    )
    if not valor:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Sin valor; ver sub-regla naturaleza_completa.",
        )
    evidencia = (Evidencia(tabla=TABLA, descripcion="general_nature", valor=valor),)
    if catalogo.naturaleza_valida(valor):
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            evidencia=evidencia,
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_DOWN,
        regla=regla,
        confianza=Confianza.PARCIAL,
        evidencia=evidencia,
        nota=f"Valor {valor!r} no matchea con el catálogo (revisar nuevo aliasing o tipo desconocido).",
    )


def _cargar_naturalezas_activas(ticket_ids: Iterable[int]) -> dict[int, list[str]]:
    """Por ticket: lista de naturalezas distintas activas en el último cambio.

    Heurística idéntica a multitópico: filas con ``coalesce(sys_audit_updated_on,
    sys_audit_created_on)`` igual al máximo del ticket → multinaturaleza real.
    Filas con timestamp anterior se descartan como historia.
    """
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    in_clause = ", ".join(str(t) for t in ids)
    rows = db.fetch(
        f"""
        SELECT
            ticket_id,
            general_nature,
            coalesce(sys_audit_updated_on, sys_audit_created_on) AS ts
        FROM {db.FQN}.`{TABLA}`
        WHERE ticket_id IN ({in_clause})
        """
    )
    por_ticket: dict[int, list[tuple[str, datetime]]] = defaultdict(list)
    for tid, valor, ts in rows:
        ts_dt = _to_datetime(ts)
        if ts_dt is None:
            continue
        v = str(valor or "").strip()
        if not v:
            continue
        por_ticket[int(tid)].append((v, ts_dt))

    out: dict[int, list[str]] = {}
    for tid, lst in por_ticket.items():
        if not lst:
            continue
        max_ts = max(ts for _, ts in lst)
        activas = sorted({v for v, ts in lst if ts == max_ts})
        out[tid] = activas
    return out


def _evaluar_multinaturaleza(
    ticket_id: int,
    naturalezas_activas: list[str] | None,
    catalogo: topicos_catalogo.Catalogo,
) -> CriterioEvaluado:
    sub_regla = "multinaturaleza_todas_validas"
    regla = (
        "Si el ticket tiene más de una naturaleza activa en el último cambio, "
        "todas deben figurar en el catálogo."
    )
    if not naturalezas_activas:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Sin filas en zendesk_ticket_nature__event para este ticket.",
        )
    if len(naturalezas_activas) <= 1:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Una sola naturaleza activa; regla no aplica.",
        )

    invalidas = [v for v in naturalezas_activas if not catalogo.naturaleza_valida(v)]
    evidencia = tuple(
        Evidencia(tabla=TABLA, descripcion="Naturaleza activa", valor=v)
        for v in naturalezas_activas
    )
    if not invalidas:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            evidencia=evidencia,
            nota=f"{len(naturalezas_activas)} naturalezas activas; todas en catálogo.",
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
            f"{len(invalidas)} de {len(naturalezas_activas)} naturalezas activas "
            f"no figuran en el catálogo: {invalidas}"
        ),
    )


def evaluar(ticket_ids: list[int]) -> list[CriterioEvaluado]:
    ultimas = _cargar_ultima_naturaleza(ticket_ids)
    activas = _cargar_naturalezas_activas(ticket_ids)
    catalogo = topicos_catalogo.cargar_catalogo()
    out: list[CriterioEvaluado] = []
    for tid in ticket_ids:
        valor = ultimas.get(tid)
        out.append(_evaluar_completitud(tid, valor))
        out.append(_evaluar_validez(tid, valor, catalogo))
        out.append(_evaluar_multinaturaleza(tid, activas.get(tid), catalogo))
    return out
