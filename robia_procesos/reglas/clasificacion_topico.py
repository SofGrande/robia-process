"""IQS — Clasificación de la conversación → Tópico / Subtópico.

Referencia: IQS Guideline (Crítico para el Negocio → Clasificación de la
conversación → Tópicos / Subtópicos). La guía pide:

- Tópico principal, secundario y subtópico **completos** y **consistentes**
  con la hoja `Tópicos y subtópicos` (Doc&Comm).
- Cuando se aplica una macro, validar que los subtópicos que pega no se
  superpongan con tópicos no aplicables al caso (esto requiere cruce con
  ``zendesk_macros_usage__event`` y queda para Fase 3).

Sub-reglas implementadas:

- ``topico_completo`` → main, secundario y subtópico no vacíos en la última
  fila de ``zendesk_ticket_topics__event``.
- ``topico_combinacion_valida`` → la tripla (main, sec, sub) figura en el
  catálogo del Sheet maestro (unión AR + LATAM + BR). Confianza *parcial*.
- ``multitopico_todas_validas`` → si el ticket es multitópico (más de una
  tripla activa en el último cambio), todas deben estar en el catálogo.
  Si solo hay una tripla activa → ``no_evaluable`` ("ticket monotópico").

- ``topico_geografia_consistente`` → la tripla está en el catálogo de la
  geografía del ticket (LATAM o BR), no solo en la unión. Detecta el caso
  "ticket BR clasificado con subtópico que solo existe en AR" (que
  ``topico_combinacion_valida`` daría thumbs_up por estar en la unión).

Sub-reglas planificadas (Fase 4):

- ``macro_subtopico_consistente`` → cruce con ``zendesk_macros_usage__event``
  + tracker de macros (Sheet) para detectar que la macro aplicada no dejó un
  subtópico inconsistente con el caso.

Heurística multitópico vs reclasificación: el lake guarda un evento por
cambio. Si todas las triplas distintas comparten el mismo timestamp máximo
de ``created_at`` → multitópico real (varias filas creadas a la vez).
Triplas con timestamp anterior se interpretan como historia (reclasificación)
y se ignoran para esta sub-regla.
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
from robia_procesos.core.geografia import Geografia, HOJAS_POR_GEO, detectar_geografia


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

CRITERIO = "Clasificación de la conversación - Tópico/Subtópico"
TABLA = "s__general__zendesk_ticket_topics__event"


def _cargar_ultimas_triplas(
    ticket_ids: Iterable[int],
) -> dict[int, tuple[str, str, str, str]]:
    """Devuelve {ticket_id: (main, sec, sub, sub_raw)} de la fila más reciente."""
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    in_clause = ", ".join(str(t) for t in ids)
    rows = db.fetch(
        f"""
        WITH x AS (
          SELECT
            ticket_id,
            main_topic_normalized,
            secondary_topic_normalized,
            subtopic_normalized,
            subtopic_raw,
            created_at,
            row_number() OVER (
              PARTITION BY ticket_id ORDER BY created_at DESC
            ) AS rw
          FROM {db.FQN}.`{TABLA}`
          WHERE ticket_id IN ({in_clause})
        )
        SELECT ticket_id,
               main_topic_normalized,
               secondary_topic_normalized,
               subtopic_normalized,
               subtopic_raw
        FROM x WHERE rw = 1
        """
    )
    return {
        int(r[0]): (
            str(r[1] or "").strip(),
            str(r[2] or "").strip(),
            str(r[3] or "").strip(),
            str(r[4] or "").strip(),
        )
        for r in rows
    }


def _evaluar_completitud(
    ticket_id: int, tripla: tuple[str, str, str, str] | None
) -> CriterioEvaluado:
    sub_regla = "topico_completo"
    regla = (
        "Tópico principal, tópico secundario y subtópico deben estar todos "
        "cargados en la última fila de zendesk_ticket_topics__event."
    )
    if tripla is None:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.DIRECTA,
            nota="Sin filas en zendesk_ticket_topics__event para este ticket.",
        )
    main, sec, sub, sub_raw = tripla
    faltantes = [
        nombre for nombre, valor in (("main", main), ("secundario", sec), ("subtopico", sub))
        if not valor
    ]
    evidencia = (
        Evidencia(
            tabla=TABLA,
            descripcion="Última tripla observada",
            valor=f"main={main!r}; sec={sec!r}; sub={sub!r}; raw={sub_raw!r}",
        ),
    )
    if faltantes:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_DOWN,
            regla=regla,
            confianza=Confianza.DIRECTA,
            evidencia=evidencia,
            nota=f"Campos vacíos: {', '.join(faltantes)}",
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_UP,
        regla=regla,
        confianza=Confianza.DIRECTA,
        evidencia=evidencia,
    )


def _evaluar_validez(
    ticket_id: int,
    tripla: tuple[str, str, str, str] | None,
    catalogo: topicos_catalogo.Catalogo,
) -> CriterioEvaluado:
    sub_regla = "topico_combinacion_valida"
    regla = (
        "La combinación (Tópico Principal → Tópico Secundario → Subtópico) "
        "debe figurar en la hoja maestra de Tópicos/Subtópicos (Doc&Comm)."
    )
    if tripla is None:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Sin filas en zendesk_ticket_topics__event para este ticket.",
        )
    main, sec, sub, sub_raw = tripla
    if not sub:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Sin subtópico cargado; ver sub-regla topico_completo.",
        )

    evidencia = (
        Evidencia(
            tabla=TABLA,
            descripcion="Tripla evaluada contra catálogo",
            valor=f"{main} / {sec} / {sub}",
        ),
    )
    if catalogo.combinacion_valida(main, sec, sub):
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            evidencia=evidencia,
        )
    sub_existe = catalogo.subtopico_existe(sub)
    nota = (
        f"Tripla no figura en catálogo. Subtópico {'sí' if sub_existe else 'no'} existe "
        "en alguna geo; revisar mapeo de tópicos principal/secundario."
    )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_DOWN,
        regla=regla,
        confianza=Confianza.PARCIAL,
        evidencia=evidencia,
        nota=nota,
    )


def _cargar_triplas_activas(
    ticket_ids: Iterable[int],
) -> dict[int, list[tuple[str, str, str, str]]]:
    """Por ticket: lista de triplas distintas (main, sec, sub, sub_raw) creadas
    en el ``max(created_at)`` del ticket.

    Filas con timestamp anterior se descartan (las consideramos historia).
    Si hay >1 tripla en el lote del último cambio → multitópico real.
    """
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    in_clause = ", ".join(str(t) for t in ids)
    rows = db.fetch(
        f"""
        SELECT
            ticket_id,
            main_topic_normalized,
            secondary_topic_normalized,
            subtopic_normalized,
            subtopic_raw,
            created_at
        FROM {db.FQN}.`{TABLA}`
        WHERE ticket_id IN ({in_clause})
        """
    )
    # agrupar por ticket conservando timestamp parseado
    por_ticket: dict[int, list[tuple[str, str, str, str, datetime]]] = defaultdict(list)
    for tid, m, s, sb, sr, ts in rows:
        ts_dt = _to_datetime(ts)
        if ts_dt is None:
            continue
        por_ticket[int(tid)].append(
            (
                str(m or "").strip(),
                str(s or "").strip(),
                str(sb or "").strip(),
                str(sr or "").strip(),
                ts_dt,
            )
        )

    out: dict[int, list[tuple[str, str, str, str]]] = {}
    for tid, lst in por_ticket.items():
        if not lst:
            continue
        max_ts = max(r[4] for r in lst)
        activas_dedup: dict[tuple[str, str, str], tuple[str, str, str, str]] = {}
        for m, s, sb, sr, ts in lst:
            if ts == max_ts:
                activas_dedup[(m, s, sb)] = (m, s, sb, sr)
        out[tid] = list(activas_dedup.values())
    return out


def _evaluar_multitopico(
    ticket_id: int,
    triplas_activas: list[tuple[str, str, str, str]] | None,
    catalogo: topicos_catalogo.Catalogo,
) -> CriterioEvaluado:
    sub_regla = "multitopico_todas_validas"
    regla = (
        "Si el ticket es multitópico (más de una tripla activa en el último "
        "cambio), todas las (main, sec, sub) deben figurar en el catálogo."
    )
    if not triplas_activas:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Sin filas en zendesk_ticket_topics__event para este ticket.",
        )
    if len(triplas_activas) <= 1:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Ticket monotópico (1 sola tripla activa); regla no aplica.",
        )

    invalidas = [
        (m, s, sb)
        for m, s, sb, _ in triplas_activas
        if not (m and s and sb and catalogo.combinacion_valida(m, s, sb))
    ]
    evidencia = tuple(
        Evidencia(
            tabla=TABLA,
            descripcion="Tripla activa (multitópico)",
            valor=f"{m} / {s} / {sb}",
        )
        for m, s, sb, _ in triplas_activas
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
            nota=f"{len(triplas_activas)} triplas activas; todas en catálogo.",
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
            f"{len(invalidas)} de {len(triplas_activas)} triplas activas no figuran "
            f"en el catálogo: {invalidas}"
        ),
    )


def _evaluar_geografia_consistente(
    ticket_id: int,
    tripla: tuple[str, str, str, str] | None,
    geo: Geografia,
    catalogo: topicos_catalogo.Catalogo,
) -> CriterioEvaluado:
    sub_regla = "topico_geografia_consistente"
    regla = (
        "La tripla (Tópico Principal → Tópico Secundario → Subtópico) debe "
        "figurar en el catálogo de la geografía del ticket (LATAM o BR), no "
        "solo en la unión global."
    )
    if tripla is None or not (tripla[0] and tripla[1] and tripla[2]):
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="Tripla incompleta; ver topico_completo.",
        )
    if geo is Geografia.DESCONOCIDA:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.PARCIAL,
            nota="No se pudo detectar la geografía del ticket (guru_name sin patrón).",
        )

    main, sec, sub, _ = tripla
    hojas_geo = HOJAS_POR_GEO[geo]
    evidencia = (
        Evidencia(
            tabla="catálogo (Sheet)",
            descripcion=f"Tripla evaluada vs hojas de {geo.value}",
            valor=f"{main} / {sec} / {sub}",
        ),
    )
    if catalogo.combinacion_valida_en_hojas(main, sec, sub, hojas_geo):
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.PARCIAL,
            evidencia=evidencia,
        )
    # ¿Existe en alguna otra hoja? Si sí → tripla cargada en geo equivocada
    if catalogo.combinacion_valida(main, sec, sub):
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_DOWN,
            regla=regla,
            confianza=Confianza.PARCIAL,
            evidencia=evidencia,
            nota=(
                f"Tripla existe en el catálogo pero no en las hojas de {geo.value} "
                f"({list(hojas_geo)}). Probable clasificación con tópico de otra geo."
            ),
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.NO_EVALUABLE,
        regla=regla,
        confianza=Confianza.PARCIAL,
        nota=(
            "Tripla no existe en ninguna geo; el problema lo cubre "
            "topico_combinacion_valida (no es problema de geografía)."
        ),
    )


def evaluar(ticket_ids: list[int]) -> list[CriterioEvaluado]:
    ultimas = _cargar_ultimas_triplas(ticket_ids)
    activas = _cargar_triplas_activas(ticket_ids)
    geos = detectar_geografia(ticket_ids)
    catalogo = topicos_catalogo.cargar_catalogo()
    out: list[CriterioEvaluado] = []
    for tid in ticket_ids:
        tripla = ultimas.get(tid)
        geo = geos.get(tid, Geografia.DESCONOCIDA)
        out.append(_evaluar_completitud(tid, tripla))
        out.append(_evaluar_validez(tid, tripla, catalogo))
        out.append(_evaluar_multitopico(tid, activas.get(tid), catalogo))
        out.append(_evaluar_geografia_consistente(tid, tripla, geo, catalogo))
    return out
