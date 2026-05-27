"""IQS — Clasificación de la conversa → sub-reglas LLM.

Compara la clasificación inferida por GPT-4o contra lo que el guru marcó en
el lake (`zendesk_ticket_topics__event` y `zendesk_ticket_nature__event`).
Reporta discrepancias.

Sub-reglas:

- ``naturaleza_inferida_vs_marcada``: si el LLM detecta una de las 3
  naturalezas cotidianas (Duda Autoatención, Duda Investigativa, Request)
  que no fue marcada por el guru, señala multinaturaleza faltante. Si el
  lake tiene Issue/Problem/Downtime, esas se evalúan vía acople con RobIA
  Solución Asertiva (ver Lección 14: regla del doble thumbs down).

- ``subtopico_inferido_vs_marcado``: si el LLM infiere subtópicos que no
  están en lake, señala multitópico faltante.

Prompt: definiciones canónicas (mezcla del canon Sofía + ejemplos operativos
del clasificador-de-tickets en testing). Estrategia de embudo SIN team_tag
en v1 — cap distribuido del catálogo (mismo target que el JS para LT). v2
agregará team_tag desde custom field del lake.

Contexto del ticket: vía Zendesk API (`get_ticket` + `get_ticket_comments`),
no del lake (el lake no almacena body).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from html import unescape
from typing import Iterable

from robia_procesos.core import db, geografia, llm, zendesk_api
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)
from robia_procesos.core.equipo_mapping import (
    GrupoSubtopicos,
    Subtopico,
    filtrar_catchall,
    filtrar_por_equipo,
)
from robia_procesos.core.geografia import Geografia
from robia_procesos.core.topicos_catalogo import (
    NATURALEZA_ALIASES_NORM,
    Catalogo,
    cargar_catalogo,
    normalizar as _normalizar,
)

CRITERIO = "Clasificación de la conversa"

# Las 3 naturalezas cotidianas que infiere el LLM.
# Issue/Problem/Downtime quedan fuera — se acoplan con Asertiva.
NATURALEZAS_LLM_VALIDAS = ("Duda Autoatención", "Duda Investigativa", "Request")

# Lake puede traer estas (en alguna geo/idioma) y NO debe contarse como
# discrepancia LLM — son responsabilidad de Asertiva.
NATURALEZAS_ACOPLADAS_ASERTIVA = ("Issue", "Problem", "Downtime")

# Substrings ya normalizadas — usadas con contains-check sobre el lake_canonica
# para detectar variantes ("Problem/Feedback", "Issue/algo", etc).
NATURALEZAS_ACOPLADAS_SUBSTRINGS = tuple(
    _normalizar(n) for n in NATURALEZAS_ACOPLADAS_ASERTIVA
)

# Cap del catálogo de subtópicos en el prompt. Mismo target que el
# clasificador-de-tickets para LT (~120 ítems distribuidos).
SUBTOPICOS_TARGET_TOTAL = 120
SUBTOPICOS_MIN_POR_GRUPO = 4

# Filtrado de comments (mismo umbral que el JS) — descarta "ok"/"gracias".
COMMENT_MIN_LEN = 80
COMMENTS_TOP_N = 3

# Cortes defensivos para no inflar prompts ni tokens.
MAX_CHARS_DESCRIPCION = 1500
MAX_CHARS_COMMENT = 1000

# Modelo + max_tokens del LLM. El prompt + catálogo puede ser largo; usamos
# response_format JSON para garantizar parseo robusto.
MAX_TOKENS_RESPUESTA = 500

SYSTEM_PROMPT_TEMPLATE = """\
Eres un evaluador IQS de Tiendanube. Geografía del ticket: {geografia_label}.

Recibís un ticket de soporte y tenés que inferir:
1. Qué naturaleza(s) de la conversa corresponden — pueden aplicar MÚLTIPLES simultáneamente.
2. Qué subtópico(s) clasifican la intención del merchant — pueden ser MÚLTIPLES si el caso cubre más de uno.

== NATURALEZA ==
LISTA VÁLIDA (texto EXACTO):
{lista_naturalezas}

DEFINICIONES:
- Duda Autoatención: duda que el guru responde con macro, KB o tutorial SIN abrir herramientas internas (stats, admin, panel administrativo). Es info general que se busca y se manda. NO requiere revisar un caso, pedido o cuenta particular del merchant.
- Duda Investigativa: requiere que el guru ABRA herramientas internas (stats, admin, panel administrativo) para revisar un caso particular del merchant — un pedido, envío, transacción, cuenta. Incluye rastreos, reclamos, seguimientos de gestiones específicas.
- Request: el guru o merchant pide EJECUTAR UNA ACCIÓN MANUAL en la tienda o cuenta del merchant. Ejemplos: reembolso, habilitación de feature, completar formulario, cancelación, cambio de datos.

HEURÍSTICA DECISIVA: Si el guru abrió una herramienta interna para revisar el caso → Investigativa. Si respondió con material existente (macro/KB) sin investigar → Autoatención. Si ejecutó una acción manual sobre la tienda o cuenta → Request. Múltiples pueden aplicar simultáneamente.

== SUBTÓPICO ==
El subtópico es lo que pide el merchant, lo que necesita. NO es el tema técnico ni las palabras clave — es la intención.

SIEMPRE elegí AL MENOS UN subtópico — si no hay coincidencia exacta, el más cercano al contexto. Usá el texto EXACTO de la lista:

{subtopicos_contexto}

LISTA DE SUBTÓPICOS VÁLIDOS (texto exacto): {lista_subtopicos}

== REGLAS DE OUTPUT ==
- Analizá el asunto y la tratativa principal. Ignorá "ok", "gracias" o cierres.
- Pueden aplicar múltiples naturalezas (ej: Investigativa + Request si revisás Y ejecutás).
- Respondé ÚNICAMENTE con JSON válido (sin texto adicional):
{{"naturaleza": ["texto exacto", ...], "subtopico": ["texto exacto", ...]}}
"""


def _naturaleza_canonica(lake_value: str | None) -> str:
    """Devuelve la forma canónica normalizada del valor del lake.

    Aplica :data:`NATURALEZA_ALIASES_NORM` para mapear "Duda/Dúvida Auto" →
    "Duda Autoatención" (forma canónica), "Problem/Feedback" → "Problem", etc.
    Si el valor no está en aliases, devuelve la normalización directa.
    """
    if not lake_value:
        return ""
    norm = _normalizar(lake_value)
    return NATURALEZA_ALIASES_NORM.get(norm, norm)


def _es_acople_asertiva(lake_canonica: str) -> bool:
    """¿La naturaleza del lake es Issue/Problem/Downtime (territorio Asertiva)?

    Usa contains-check para tolerar variantes que el alias no cubre
    (ej. "issue_xyz", "problem_general", etc).
    """
    return any(sub in lake_canonica for sub in NATURALEZAS_ACOPLADAS_SUBSTRINGS)


def _limpiar_html(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _ticket_context_y_equipo(ticket_id: int) -> tuple[str, str | None]:
    """Una sola llamada ``get_ticket`` para extraer contexto + equipo.

    Devuelve ``(contexto_para_prompt, equipo_zendesk_o_None)``.
    El equipo viene del custom field ``9204146951188`` (formato Zendesk:
    snake_case, ej ``"pago_nube_transacciones_y_cuenta"``).
    """
    ticket = zendesk_api.get_ticket(ticket_id)
    subject = ticket.get("subject") or ""
    description = _limpiar_html(ticket.get("description"))

    equipo = None
    for cf in ticket.get("custom_fields", []) or []:
        if cf.get("id") == zendesk_api.FIELD_ID_EQUIPO:
            v = cf.get("value")
            if v not in (None, "", False):
                equipo = str(v)
            break

    comments = zendesk_api.get_ticket_comments(
        ticket_id, per_page=10, sort_order="desc"
    )
    sustantivos: list[str] = []
    for c in comments:
        body = _limpiar_html(c.get("body"))
        if len(body) >= COMMENT_MIN_LEN:
            sustantivos.append(body[:MAX_CHARS_COMMENT])
        if len(sustantivos) >= COMMENTS_TOP_N:
            break

    partes = [f"Asunto: {subject}"]
    if description:
        partes.append(f"\nDescripción inicial:\n{description[:MAX_CHARS_DESCRIPCION]}")
    if sustantivos:
        partes.append("\nÚltimos comentarios sustantivos:")
        for i, c in enumerate(sustantivos, 1):
            partes.append(f"[{i}] {c}")
    return "\n".join(partes), equipo


def _agrupar_subtopicos_por_main(
    combinaciones: Iterable[tuple[str, str, str, str]],
) -> list[GrupoSubtopicos]:
    """Agrupa cuartetos (equipo, main, sec, sub) en GrupoSubtopicos[main_topic].

    El ``equipo`` queda asociado a cada :class:`Subtopico` para que
    :func:`filtrar_por_equipo` pueda usarlo. Si una combinación no tiene equipo
    (LATAM genérico), el Subtópico queda con ``equipo=None``.
    """
    por_main: dict[str, list[Subtopico]] = defaultdict(list)
    seen: set[str] = set()
    for equipo, main, sec, sub in combinaciones:
        if not (main and sec and sub):
            continue
        label = f"{main}:: {sec}:: {sub}"
        if label in seen:
            continue
        seen.add(label)
        por_main[main].append(
            Subtopico(label=label, equipo=equipo if equipo else None)
        )
    grupos = [
        GrupoSubtopicos(label=main, options=tuple(opts))
        for main, opts in por_main.items()
    ]
    return filtrar_catchall(grupos)


def _cap_distribuido(
    grupos: list[GrupoSubtopicos],
    target_total: int,
    min_por_grupo: int,
) -> list[GrupoSubtopicos]:
    """Cap distribuido — mismo algoritmo que el clasificador JS para LT."""
    n = max(len(grupos), 1)
    por_grupo = max(min_por_grupo, target_total // n)
    capped: list[GrupoSubtopicos] = []
    for g in grupos:
        opts = g.options[:por_grupo]
        if opts:
            capped.append(GrupoSubtopicos(label=g.label, options=opts))
    return capped


_GEO_LABELS: dict[Geografia, str] = {
    Geografia.LATAM: "LATAM (Tiendanube AR/MX) — responder en español",
    Geografia.BR: "Brasil (Nuvemshop) — responder en portugués brasileño",
    Geografia.DESCONOCIDA: "no identificada — responder en español por defecto",
}


def _build_system_prompt(grupos: list[GrupoSubtopicos], geo: Geografia) -> str:
    lista_naturalezas = "\n".join(f'- "{n}"' for n in NATURALEZAS_LLM_VALIDAS)
    contexto_partes: list[str] = []
    all_labels: list[str] = []
    for g in grupos:
        lineas = [f"  - {opt.label}" for opt in g.options]
        contexto_partes.append(f"[{g.label}]\n" + "\n".join(lineas))
        all_labels.extend(opt.label for opt in g.options)
    return SYSTEM_PROMPT_TEMPLATE.format(
        geografia_label=_GEO_LABELS.get(geo, _GEO_LABELS[Geografia.DESCONOCIDA]),
        lista_naturalezas=lista_naturalezas,
        subtopicos_contexto="\n\n".join(contexto_partes),
        lista_subtopicos=", ".join(all_labels),
    )


def _construir_grupos_para_geo(
    catalogo: Catalogo, hojas: tuple[str, ...]
) -> list[GrupoSubtopicos]:
    """Construye grupos filtrando por hojas del Sheet (geografía).

    Conserva el atributo ``equipo`` en cada Subtópico (necesario para que
    :func:`filtrar_por_equipo` funcione después).

    Si ``hojas`` está vacía (Geografia.DESCONOCIDA), retorna el catálogo
    completo como fallback — mejor pasar todas las opciones al LLM que
    quedarse sin contexto.
    """
    if not hojas:
        return _agrupar_subtopicos_por_main(catalogo.combinaciones_con_equipo)

    claves_validas: set[tuple[str, str, str]] = set()
    for hoja in hojas:
        claves_validas |= catalogo.combinaciones_por_hoja.get(hoja, frozenset())
    if not claves_validas:
        return _agrupar_subtopicos_por_main(catalogo.combinaciones_con_equipo)

    filtradas = [
        (eq, m, s, sub)
        for eq, m, s, sub in catalogo.combinaciones_con_equipo
        if (_normalizar(m), _normalizar(s), _normalizar(sub)) in claves_validas
    ]
    return _agrupar_subtopicos_por_main(filtradas)


def _clasificar_llm(contexto: str, system_prompt: str) -> dict:
    raw = llm.chat(
        system=system_prompt,
        user=contexto,
        response_format={"type": "json_object"},
        max_tokens=MAX_TOKENS_RESPUESTA,
    )
    return json.loads(raw)


def _guru_evaluado(ticket_ids: Iterable[int]) -> dict[int, str | None]:
    """Devuelve {ticket_id: guru_name del último assignee}.

    Mono-guru hoy (criterio del repo padre ``docs/GURU_TEAM_LOGIC.md``):
    el guru responsable de la percepción final del ticket es quien recibió
    la última asignación. Multi-guru scoring queda en backlog del repo padre.
    """
    ids = sorted({int(t) for t in ticket_ids})
    if not ids:
        return {}
    in_clause = ", ".join(str(t) for t in ids)
    rows = db.fetch(
        f"""
        WITH a AS (
          SELECT
            ticket_id,
            guru_name,
            row_number() OVER (
              PARTITION BY ticket_id ORDER BY assignment_start_time DESC
            ) AS rw
          FROM {db.FQN}.`s__general__zendesk_assignment__event`
          WHERE ticket_id IN ({in_clause}) AND guru_name IS NOT NULL
        )
        SELECT ticket_id, guru_name FROM a WHERE rw = 1
        """
    )
    out: dict[int, str | None] = {tid: None for tid in ids}
    for r in rows:
        out[int(r[0])] = r[1]
    return out


def _evidencia_guru(guru_name: str | None) -> Evidencia:
    return Evidencia(
        tabla="zendesk_assignment__event",
        descripcion="Guru evaluado (último assignee)",
        valor=guru_name or "(sin assignee)",
    )


def _lake_topics(ticket_id: int) -> list[tuple[str, str, str]]:
    rows = db.fetch(
        f"""
        SELECT main_topic_normalized, secondary_topic_normalized, subtopic_normalized
        FROM {db.FQN}.`s__general__zendesk_ticket_topics__event`
        WHERE ticket_id = {int(ticket_id)}
        """
    )
    return [(m or "", s or "", sub or "") for m, s, sub in rows if sub]


def _lake_nature(ticket_id: int) -> str | None:
    rows = db.fetch(
        f"""
        SELECT general_nature
        FROM {db.FQN}.`s__general__zendesk_ticket_nature__event`
        WHERE ticket_id = {int(ticket_id)}
        ORDER BY sys_audit_updated_on DESC
        LIMIT 1
        """
    )
    if not rows:
        return None
    v = (rows[0][0] or "").strip()
    return v or None


def _evaluar_naturaleza(
    ticket_id: int,
    guru_name: str | None,
    lake_nature: str | None,
    nat_llm: list[str],
) -> CriterioEvaluado:
    sub_regla = "naturaleza_inferida_vs_marcada"
    regla = (
        "El guru debe marcar las naturalezas cotidianas que correspondan al caso. "
        "LLM infiere 3 (Auto/Investigativa/Request); Issue/Problem/Downtime se "
        "evalúan vía acople con RobIA Solución Asertiva (ver Lección 14)."
    )
    evidencia = (
        _evidencia_guru(guru_name),
        Evidencia(
            tabla="zendesk_ticket_nature__event",
            descripcion="general_nature en lake",
            valor=lake_nature or "(sin marcar)",
        ),
        Evidencia(
            tabla="LLM (clasificacion_llm)",
            descripcion="Naturalezas inferidas",
            valor=", ".join(nat_llm) or "(ninguna)",
        ),
    )

    lake_canonica = _naturaleza_canonica(lake_nature)
    llm_norms = {_normalizar(n) for n in nat_llm if n}
    acople = _es_acople_asertiva(lake_canonica)

    if not lake_nature and not nat_llm:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota="Sin naturaleza marcada en lake y LLM no detectó cotidianas.",
        )
    if lake_nature and not nat_llm:
        if acople:
            # Lake dice Issue/Problem/Downtime y el LLM no detectó cotidianas
            # adicionales → consistente con que el caso era solo I/P/D.
            return CriterioEvaluado(
                ticket_id=ticket_id,
                criterio=CRITERIO,
                sub_regla=sub_regla,
                resultado=Resultado.THUMBS_UP,
                regla=regla,
                confianza=Confianza.HEURISTICA,
                evidencia=evidencia,
                nota=(
                    f"Lake='{lake_nature}' (acople con Asertiva). LLM no detectó "
                    "cotidianas adicionales — sin multinaturaleza faltante."
                ),
            )
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota=f"Lake='{lake_nature}'; LLM no clasificó cotidianas.",
        )
    if not lake_nature and nat_llm:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_DOWN,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota=f"Lake sin naturaleza; LLM infiere: {nat_llm}.",
        )
    # Ambos con valor
    if lake_canonica in llm_norms:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota=f"Coincide: Lake='{lake_nature}' (canónica), LLM={nat_llm}.",
        )
    if acople:
        # Lake marca Issue/Problem/Downtime — evaluado por Asertiva. El LLM
        # detectó cotidianas adicionales no marcadas → multinaturaleza faltante.
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_DOWN,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota=(
                f"Lake='{lake_nature}' (acople con Asertiva). LLM detectó "
                f"naturalezas cotidianas no marcadas por el guru: {nat_llm}. "
                "Posible multinaturaleza faltante."
            ),
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.THUMBS_DOWN,
        regla=regla,
        confianza=Confianza.HEURISTICA,
        evidencia=evidencia,
        nota=f"Discrepancia: Lake='{lake_nature}' vs LLM={nat_llm}.",
    )


def _evaluar_subtopico(
    ticket_id: int,
    guru_name: str | None,
    lake_topics: list[tuple[str, str, str]],
    sub_llm: list[str],
    modo_filtrado: str,
) -> CriterioEvaluado:
    sub_regla = "subtopico_inferido_vs_marcado"
    regla = (
        "El guru debe marcar todos los subtópicos que apliquen al caso. Si LLM "
        "infiere subtópicos no marcados en el lake, es señal de multitópico faltante."
    )
    lake_labels = [f"{m}:: {s}:: {sub}" for m, s, sub in lake_topics]
    evidencia = (
        _evidencia_guru(guru_name),
        Evidencia(
            tabla="LLM (clasificacion_llm)",
            descripcion="Filtrado del catálogo pasado al LLM",
            valor=modo_filtrado,
        ),
        Evidencia(
            tabla="zendesk_ticket_topics__event",
            descripcion="Subtópicos marcados en lake",
            valor=" | ".join(lake_labels) or "(ninguno)",
        ),
        Evidencia(
            tabla="LLM (clasificacion_llm)",
            descripcion="Subtópicos inferidos",
            valor=" | ".join(sub_llm) or "(ninguno)",
        ),
    )
    lake_norms = {_normalizar(lbl) for lbl in lake_labels}
    llm_norms = {_normalizar(s) for s in sub_llm if s}

    if not lake_topics and not sub_llm:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.NO_EVALUABLE,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota="Sin subtópicos en lake ni inferidos.",
        )
    coincidencias = lake_norms & llm_norms
    solo_llm = llm_norms - lake_norms

    if coincidencias and not solo_llm:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_UP,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota=f"LLM coincide con los {len(lake_topics)} subtópico(s) del lake.",
        )
    if solo_llm:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sub_regla,
            resultado=Resultado.THUMBS_DOWN,
            regla=regla,
            confianza=Confianza.HEURISTICA,
            evidencia=evidencia,
            nota=(
                f"LLM detectó subtópicos no marcados en lake. "
                f"Lake={lake_labels or '(ninguno)'}; LLM={sub_llm}. "
                "Posible multitópico faltante u opción mal elegida por el guru."
            ),
        )
    # solo lake (LLM no detectó algunos del lake) — capaz catálogo cap-distribuido
    # se quedó corto o el LLM no vio el caso completo.
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla=sub_regla,
        resultado=Resultado.NO_EVALUABLE,
        regla=regla,
        confianza=Confianza.HEURISTICA,
        evidencia=evidencia,
        nota=(
            f"LLM no inferió subtópicos que están marcados en lake. "
            f"Lake={lake_labels}; LLM={sub_llm}. Capaz el catálogo cap-distribuido "
            "no incluyó la opción del lake — confirmar en v2 con team_tag."
        ),
    )


def evaluar(ticket_ids: list[int]) -> list[CriterioEvaluado]:
    catalogo = cargar_catalogo()
    geos_por_ticket = geografia.detectar_geografia(ticket_ids)
    gurus_por_ticket = _guru_evaluado(ticket_ids)

    # Pre-construir grupos por geografía (sin cap, completos).
    # El filtrado por equipo (per-ticket) se aplica encima de esto.
    grupos_por_geo: dict[Geografia, list[GrupoSubtopicos]] = {}
    for geo in set(geos_por_ticket.values()) | {Geografia.DESCONOCIDA}:
        hojas = geografia.HOJAS_POR_GEO.get(geo, ())
        grupos_por_geo[geo] = _construir_grupos_para_geo(catalogo, hojas)

    resultados: list[CriterioEvaluado] = []
    for tid in ticket_ids:
        lake_topics = _lake_topics(tid)
        lake_nature = _lake_nature(tid)
        geo = geos_por_ticket.get(tid, Geografia.DESCONOCIDA)
        guru_name = gurus_por_ticket.get(tid)

        # Una sola llamada Zendesk: contexto + equipo del ticket.
        try:
            contexto, equipo_ticket = _ticket_context_y_equipo(tid)
        except Exception as e:
            resultados.append(
                CriterioEvaluado(
                    ticket_id=tid,
                    criterio=CRITERIO,
                    sub_regla="clasificacion_llm_failed",
                    resultado=Resultado.NO_EVALUABLE,
                    regla="Falló get_ticket en Zendesk API (ticket inaccesible o token expirado).",
                    confianza=Confianza.HEURISTICA,
                    evidencia=(_evidencia_guru(guru_name),),
                    nota=f"{type(e).__name__}: {e}",
                )
            )
            continue

        # Estrategia del embudo: filtrar catálogo al equipo del ticket si matchea.
        # Si no hay equipo o no matchea → cap distribuido como fallback.
        grupos_geo = grupos_por_geo[geo]
        por_equipo = filtrar_por_equipo(grupos_geo, equipo_ticket) if equipo_ticket else None
        if por_equipo:
            grupos_finales = por_equipo
            modo_filtrado = f"equipo='{equipo_ticket}'"
        else:
            grupos_finales = _cap_distribuido(
                grupos_geo, SUBTOPICOS_TARGET_TOTAL, SUBTOPICOS_MIN_POR_GRUPO
            )
            modo_filtrado = (
                f"cap distribuido (sin match equipo='{equipo_ticket}')"
                if equipo_ticket
                else "cap distribuido (sin team_tag)"
            )

        system_prompt = _build_system_prompt(grupos_finales, geo)

        try:
            inferido = _clasificar_llm(contexto, system_prompt)
        except Exception as e:
            resultados.append(
                CriterioEvaluado(
                    ticket_id=tid,
                    criterio=CRITERIO,
                    sub_regla="clasificacion_llm_failed",
                    resultado=Resultado.NO_EVALUABLE,
                    regla="Inferencia LLM falló (problema de API o JSON inválido).",
                    confianza=Confianza.HEURISTICA,
                    evidencia=(_evidencia_guru(guru_name),),
                    nota=f"{type(e).__name__}: {e}  ({modo_filtrado})",
                )
            )
            continue

        nat_llm = [n.strip() for n in (inferido.get("naturaleza") or []) if n]
        sub_llm = [s.strip() for s in (inferido.get("subtopico") or []) if s]

        resultados.append(_evaluar_naturaleza(tid, guru_name, lake_nature, nat_llm))
        resultados.append(
            _evaluar_subtopico(tid, guru_name, lake_topics, sub_llm, modo_filtrado)
        )

    return resultados
