"""IQS — Procesos Zendesk → Duplicates (B3 Minimum, 4 sub-reglas determinísticas).

Detecta el patrón de merge de Zendesk via regex sobre los comments del
ticket auditado. Si encuentra notificación de merge, el ticket es **kept**
(conservado) y los IDs referenciados son los duplicados cerrados.

Sub-reglas:
    - macro_cierre_aplicada      → 3.2: ¿se aplicó la macro de cierre duplicado
                                   ANTES del cierre del duplicado?
    - fusion_al_mas_antiguo      → 3.3: ¿el kept es más antiguo que los duplicados?
                                   (solo aplica si no hay WA en la pareja)
    - merge_notification_privada → 3.4: ¿la notificación de merge quedó como
                                   nota interna (no pública)?
    - priorizo_whatsapp          → 3.5: si había WA en la pareja, ¿fue elegido
                                   como kept?

Sub-regla 3.1 (no detectó duplicado existente) NO se implementa acá — requiere
LLM y queda para B3 Standard en otra sesión.

Cero LLM. Solo Zendesk API + 1-N queries al lake (por duplicado referenciado).
"""
from __future__ import annotations

import re
from datetime import datetime

from robia_procesos.core import db, zendesk_api as zd
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Resultado,
)

CRITERIO = "Duplicates"

# Regex base de detección del merge de Zendesk. Texto BR-PT fijo en cualquier
# geografía (confirmado en discovery 03 contra tickets AR/BR).
REGEX_MERGE = re.compile(
    r"A solicita[çc][ãa]o #(\d+).*?foi fechada e fundida com esta solicita",
    re.IGNORECASE | re.DOTALL,
)

# Macros de cierre duplicado por geografía (memoria reference_ids_zendesk).
# LATAM usa la macro de AR según calibración Sofía.
MACRO_CIERRE_DUPLICADO_AR = 35553003184020
MACRO_CIERRE_DUPLICADO_BR = 15965574871828
MACROS_CIERRE_DUPLICADO = {MACRO_CIERRE_DUPLICADO_AR, MACRO_CIERRE_DUPLICADO_BR}


# ────────────────────── Detección de merge ──────────────────────


def _detectar_merges(comments: list[dict]) -> list[dict]:
    """Devuelve la lista de duplicados detectados en los comments del ticket.

    Cada elemento: {'duplicate_id': int, 'public': bool, 'created_at': str}.
    Si no hay merge en los comments, devuelve [].
    """
    merges = []
    for c in comments:
        body = (c.get("plain_body") or c.get("body") or "")
        for match in REGEX_MERGE.finditer(body):
            try:
                dup_id = int(match.group(1))
            except ValueError:
                continue
            merges.append({
                "duplicate_id": dup_id,
                "public": bool(c.get("public", False)),
                "created_at": c.get("created_at"),
            })
    return merges


def _macros_aplicadas(ticket_id: int) -> set[int]:
    """Devuelve el set de macro_ids aplicadas al ticket (vía lake)."""
    sql = f"""
        SELECT DISTINCT macro_id
        FROM {db.FQN}.`s__general__zendesk_macros_usage__event`
        WHERE ticket_id = {ticket_id}
    """
    try:
        with db.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall() or []
        return {int(r[0]) for r in rows if r[0] is not None}
    except Exception:
        return set()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ────────────────────── Sub-reglas ──────────────────────


def _sin_merge_response(ticket_id: int, motivo: str) -> list[CriterioEvaluado]:
    """Cuando no hubo merge detectado: las 4 sub-reglas son NO_EVALUABLE."""
    return [
        CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla=sr,
            resultado=Resultado.NO_EVALUABLE,
            regla=motivo,
            confianza=Confianza.DIRECTA,
        )
        for sr in (
            "macro_cierre_aplicada",
            "fusion_al_mas_antiguo",
            "merge_notification_privada",
            "priorizo_whatsapp",
        )
    ]


def _sub_macro_cierre(
    ticket_id: int,
    duplicates_data: list[dict],
) -> CriterioEvaluado:
    """3.2: ¿se aplicó macro de cierre duplicado en CADA ticket duplicado cerrado?"""
    sin_macro = []
    for d in duplicates_data:
        macros = _macros_aplicadas(d["id"])
        if not (macros & MACROS_CIERRE_DUPLICADO):
            sin_macro.append(d["id"])

    if not sin_macro:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="macro_cierre_aplicada",
            resultado=Resultado.THUMBS_UP,
            regla="Duplicates: La macro de cierre duplicado se aplicó correctamente en todos los tickets fusionados.",
            confianza=Confianza.DIRECTA,
        )

    refs = ", ".join(f"#{tid}" for tid in sin_macro[:3])
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="macro_cierre_aplicada",
        resultado=Resultado.THUMBS_DOWN,
        regla=(
            f"Duplicates: No se aplicó la macro de cierre duplicado en "
            f"{len(sin_macro)} ticket(s) fusionado(s) ({refs}). El guru debió "
            "aplicar 'Cerrar conversa duplicada' antes de fusionar."
        ),
        confianza=Confianza.DIRECTA,
    )


def _sub_merge_notif_privada(
    ticket_id: int,
    merges: list[dict],
) -> CriterioEvaluado:
    """3.4: ¿la notificación de merge quedó como nota interna?"""
    publicas = [m for m in merges if m["public"]]
    if not publicas:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="merge_notification_privada",
            resultado=Resultado.THUMBS_UP,
            regla="Duplicates: La notificación de fusión quedó como nota interna, no visible al cliente.",
            confianza=Confianza.DIRECTA,
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="merge_notification_privada",
        resultado=Resultado.THUMBS_DOWN,
        regla=(
            f"Duplicates: La notificación de fusión quedó como respuesta "
            f"PÚBLICA en el ticket, visible al cliente. El detalle técnico "
            "del merge debió ser nota interna."
        ),
        confianza=Confianza.DIRECTA,
    )


def _sub_priorizo_whatsapp(
    ticket_id: int,
    kept_via_channel: str | None,
    duplicates_data: list[dict],
) -> CriterioEvaluado:
    """3.5: si había WA en la pareja, ¿fue elegido como kept?"""
    duplicados_wa = [d for d in duplicates_data if d.get("via_channel") == "whatsapp"]
    if not duplicados_wa:
        # No había WA en ningún duplicado → no aplica
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="priorizo_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla="Duplicates: No había canal WhatsApp en la pareja de duplicados.",
            confianza=Confianza.DIRECTA,
        )

    if kept_via_channel == "whatsapp":
        # Kept es WA → priorizó correctamente
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="priorizo_whatsapp",
            resultado=Resultado.THUMBS_UP,
            regla="Duplicates: Se priorizó correctamente el ticket de WhatsApp en la fusión.",
            confianza=Confianza.DIRECTA,
        )

    # Había duplicados WA pero el kept NO es WA → error
    refs = ", ".join(f"#{d['id']}" for d in duplicados_wa[:3])
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="priorizo_whatsapp",
        resultado=Resultado.THUMBS_DOWN,
        regla=(
            f"Duplicates: Había {len(duplicados_wa)} ticket(s) de WhatsApp "
            f"en la pareja ({refs}) pero la fusión se hizo en sentido "
            "opuesto. El kept debió ser el ticket de WhatsApp."
        ),
        confianza=Confianza.DIRECTA,
    )


def _sub_fusion_al_mas_antiguo(
    ticket_id: int,
    kept_created_at: datetime | None,
    kept_via_channel: str | None,
    duplicates_data: list[dict],
) -> CriterioEvaluado:
    """3.3: ¿el kept es más antiguo que los duplicados? (solo si no hay WA)."""
    # Si hay WA en la pareja, la regla 3.5 prevalece — esta no aplica.
    hay_wa = (kept_via_channel == "whatsapp") or any(
        d.get("via_channel") == "whatsapp" for d in duplicates_data
    )
    if hay_wa:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_al_mas_antiguo",
            resultado=Resultado.NO_EVALUABLE,
            regla="Duplicates: Hay WhatsApp en la pareja; prevalece la regla de priorizar WA sobre antigüedad.",
            confianza=Confianza.DIRECTA,
        )

    if kept_created_at is None:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_al_mas_antiguo",
            resultado=Resultado.NO_EVALUABLE,
            regla="Duplicates: No se pudo determinar la fecha de creación del ticket.",
            confianza=Confianza.HEURISTICA,
        )

    duplicados_mas_viejos = []
    for d in duplicates_data:
        dup_created = _parse_iso(d.get("created_at_ticket"))
        if dup_created and dup_created < kept_created_at:
            duplicados_mas_viejos.append(d["id"])

    if not duplicados_mas_viejos:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_al_mas_antiguo",
            resultado=Resultado.THUMBS_UP,
            regla="Duplicates: La fusión se hizo correctamente al ticket más antiguo.",
            confianza=Confianza.DIRECTA,
        )

    refs = ", ".join(f"#{tid}" for tid in duplicados_mas_viejos[:3])
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="fusion_al_mas_antiguo",
        resultado=Resultado.THUMBS_DOWN,
        regla=(
            f"Duplicates: Se fusionó al ticket más nuevo. Existían {len(duplicados_mas_viejos)} "
            f"duplicado(s) más antiguo(s) ({refs}) que debieron ser el kept."
        ),
        confianza=Confianza.DIRECTA,
    )


# ────────────────────── Entry point ──────────────────────


def evaluar_duplicates(ticket_id: int) -> list[CriterioEvaluado]:
    """Corre las 4 sub-reglas de Duplicates sobre 1 ticket."""
    try:
        comments = zd.get_ticket_comments(ticket_id, per_page=50, sort_order="asc")
    except Exception as e:
        return [
            CriterioEvaluado(
                ticket_id=ticket_id,
                criterio=CRITERIO,
                sub_regla=sr,
                resultado=Resultado.NO_EVALUABLE,
                regla=f"Duplicates: No se pudieron leer los comments del ticket — {type(e).__name__}.",
                confianza=Confianza.HEURISTICA,
            )
            for sr in (
                "macro_cierre_aplicada",
                "fusion_al_mas_antiguo",
                "merge_notification_privada",
                "priorizo_whatsapp",
            )
        ]

    merges = _detectar_merges(comments)
    if not merges:
        return _sin_merge_response(
            ticket_id,
            "Duplicates: Este ticket no fue kept de ninguna fusión.",
        )

    # Es kept de merge — obtener data del propio ticket + cada duplicado
    try:
        kept_ticket = zd.get_ticket(ticket_id)
    except Exception:
        kept_ticket = {}

    kept_via = (kept_ticket.get("via") or {}).get("channel")
    kept_created_at = _parse_iso(kept_ticket.get("created_at"))

    duplicates_data = []
    for m in merges:
        try:
            dup = zd.get_ticket(m["duplicate_id"])
        except Exception:
            continue
        duplicates_data.append({
            "id": m["duplicate_id"],
            "via_channel": (dup.get("via") or {}).get("channel"),
            "created_at_ticket": dup.get("created_at"),
            "public_merge_notif": m["public"],
        })

    return [
        _sub_macro_cierre(ticket_id, duplicates_data),
        _sub_fusion_al_mas_antiguo(
            ticket_id, kept_created_at, kept_via, duplicates_data
        ),
        _sub_merge_notif_privada(ticket_id, merges),
        _sub_priorizo_whatsapp(ticket_id, kept_via, duplicates_data),
    ]
