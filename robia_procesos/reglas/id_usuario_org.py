"""IQS — Procesos Zendesk → Id Usuario / Organización (4 sub-reglas determinísticas).

Las 4 sub-reglas se evalúan vía Zendesk API exclusivamente (el lake no
trackea los custom fields que necesitamos — confirmado en discovery 2026-05-27).

Sub-reglas:
    2.1 fusion_usuario_whatsapp
    2.2 organizacion_asociada      ← regla canónica Sofía: TODO ticket DEBE tener org
    2.3a es_partner_checkbox       ← solo aplica si el ticket es de partner
    2.3b partner_id_cargado        ← solo aplica si el ticket es de partner

Cero LLM, cero queries al lake.
"""
from __future__ import annotations

import re
from functools import lru_cache

from robia_procesos.core import zendesk_api as zd
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)

CRITERIO = "Procesos Zendesk - Id Usuario/Org"

# Custom field IDs (ver memoria reference_ids_zendesk)
FIELD_ES_PARTNER = 9470656687892        # checkbox "Es partner?"
FIELD_PARTNER_ID = 33512019928340       # integer "[SOL] Partner ID"

# Grupos canónicos de Partners (group_id de Zendesk).
# Aclaración Sofía 2026-05-27: el equipo "success_equipe" NO es indicador de
# partner — atiende top/large sellers. Solo estos 4 grupos son partner.
PARTNER_GROUPS: dict[int, str] = {
    1900001463407: "[AR] Partners",
    4412845735316: "[AR] Partners Pagos",
    1900001463187: "[BR] Partners",
    23762711076756: "[Latam] Partners Pagos",
}

# Regex para detectar partner en notas internas (stats.tiendanube.com/partner/profile?id=)
REGEX_PARTNER_URL = re.compile(
    r"stats\.tiendanube\.com/partner/profile\?id=\d+", re.IGNORECASE
)


@lru_cache(maxsize=256)
def _cf(ticket: tuple, field_id: int) -> object:
    """Lookup de custom field. ticket es una tupla hasheable (id, frozen dict)."""
    # truco: no usar lru_cache acá, mejor pasamos el ticket completo y buscamos manual
    raise NotImplementedError("use _custom_field_value directly")


def _custom_field_value(ticket: dict, field_id: int) -> object:
    """Devuelve el value del custom field, o None si no está o es vacío."""
    for cf in ticket.get("custom_fields", []) or []:
        if cf.get("id") == field_id:
            v = cf.get("value")
            if v in (None, "", False, []):
                return None
            return v
    return None


def _es_ticket_de_partner(ticket: dict, comments: list[dict]) -> tuple[bool, str]:
    """¿Este ticket es atendido por un equipo de Partners?

    Disparador principal (canónico Sofía 2026-05-27):
        group_id del ticket ∈ PARTNER_GROUPS

    Disparador secundario (refuerzo): URL stats.tiendanube.com/partner/profile
    en una nota interna — solo aplica si group_id ya matcheó, para no
    generar falsos positivos por gurus que linkean perfiles ocasionalmente.

    Returns:
        (es_partner: bool, razón: str)
    """
    group_id = ticket.get("group_id")
    if group_id in PARTNER_GROUPS:
        return True, f"group_id={group_id} ({PARTNER_GROUPS[group_id]})"
    return False, ""


# ────────────────────── Sub-regla 2.2 — Organización asociada ──────────────────────


def _evaluar_organizacion(ticket_id: int, ticket: dict) -> CriterioEvaluado:
    """Regla canónica Sofía: TODO ticket DEBE tener organización asociada."""
    org_id = ticket.get("organization_id")
    if org_id:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="organizacion_asociada",
            resultado=Resultado.THUMBS_UP,
            regla=f"Organización asociada (id={org_id})",
            confianza=Confianza.DIRECTA,
        )
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="organizacion_asociada",
        resultado=Resultado.THUMBS_DOWN,
        regla="No asoció organización",
        confianza=Confianza.DIRECTA,
        nota="Regla canónica: todo ticket debe tener organización asociada.",
    )


# ────────────────────── Sub-regla 2.1 — Fusión usuario WhatsApp ──────────────────────


def _evaluar_fusion_usuario(ticket_id: int, ticket: dict) -> CriterioEvaluado:
    """Detecta caso WA con user no fusionado.

    Aplica solo cuando:
      - via.channel == 'whatsapp'
      - organization_id está poblada (sin org no podemos buscar otros users)

    Detección: si el requester actual no tiene email Y existe otro user en la
    misma org con email → THUMBS_DOWN.
    """
    via_channel = (ticket.get("via") or {}).get("channel")
    if via_channel != "whatsapp":
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla="Canal no es WhatsApp; sub-regla no aplica",
            confianza=Confianza.DIRECTA,
        )

    org_id = ticket.get("organization_id")
    if not org_id:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla="Canal WA pero sin organización — no se puede buscar duplicados",
            confianza=Confianza.DIRECTA,
            nota="Para auditar fusión WA hace falta organización asociada (ver sub-regla 2.2).",
        )

    requester_id = ticket.get("requester_id")
    try:
        requester = zd.get_user(requester_id) if requester_id else {}
    except Exception as e:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla=f"Error consultando requester: {e}",
            confianza=Confianza.HEURISTICA,
        )

    if requester.get("email"):
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.THUMBS_UP,
            regla="Canal WA pero requester ya tiene email — no requiere fusión",
            confianza=Confianza.DIRECTA,
        )

    # Buscar otros usuarios de la org con email
    try:
        users = zd.search_users_by_org(org_id)
    except Exception as e:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla=f"Error buscando usuarios de la org: {e}",
            confianza=Confianza.HEURISTICA,
        )

    otros_con_email = [
        u for u in users
        if u.get("id") != requester_id and u.get("email")
    ]
    if otros_con_email:
        nombres = ", ".join(u.get("email", "?") for u in otros_con_email[:3])
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.THUMBS_DOWN,
            regla="No fusionó usuario WA con cuenta email existente",
            confianza=Confianza.PARCIAL,
            nota=f"Existen otros users en la org con email: {nombres}",
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="fusion_usuario_whatsapp",
        resultado=Resultado.THUMBS_UP,
        regla="WA sin email, pero no hay otro user en la org con email — sin candidato a fusión",
        confianza=Confianza.PARCIAL,
    )


# ────────────────────── Sub-reglas 2.3a y 2.3b — Partner ──────────────────────


def _evaluar_partner_checkbox(
    ticket_id: int,
    ticket: dict,
    es_partner: bool,
    razon_partner: str,
) -> CriterioEvaluado:
    """¿Tildó el checkbox 'Es partner?' cuando el ticket es de un partner?"""
    if not es_partner:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="es_partner_checkbox",
            resultado=Resultado.NO_EVALUABLE,
            regla="Ticket no identificado como partner — sub-regla no aplica",
            confianza=Confianza.DIRECTA,
        )

    es_partner_val = _custom_field_value(ticket, FIELD_ES_PARTNER)
    if es_partner_val is True:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="es_partner_checkbox",
            resultado=Resultado.THUMBS_UP,
            regla="Checkbox 'Es partner?' tildado correctamente",
            confianza=Confianza.DIRECTA,
            nota=f"Identificado como partner por: {razon_partner}",
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="es_partner_checkbox",
        resultado=Resultado.THUMBS_DOWN,
        regla="No tildó checkbox 'Es partner?'",
        confianza=Confianza.DIRECTA,
        nota=f"Identificado como partner por: {razon_partner}",
    )


def _evaluar_partner_id(
    ticket_id: int,
    ticket: dict,
    es_partner: bool,
    razon_partner: str,
) -> CriterioEvaluado:
    """¿Cargó el Partner ID cuando el ticket es de un partner?"""
    if not es_partner:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="partner_id_cargado",
            resultado=Resultado.NO_EVALUABLE,
            regla="Ticket no identificado como partner — sub-regla no aplica",
            confianza=Confianza.DIRECTA,
        )

    partner_id = _custom_field_value(ticket, FIELD_PARTNER_ID)
    if partner_id:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="partner_id_cargado",
            resultado=Resultado.THUMBS_UP,
            regla=f"Partner ID cargado (={partner_id})",
            confianza=Confianza.DIRECTA,
            nota=f"Identificado como partner por: {razon_partner}",
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="partner_id_cargado",
        resultado=Resultado.THUMBS_DOWN,
        regla="No cargó Partner ID",
        confianza=Confianza.DIRECTA,
        nota=f"Identificado como partner por: {razon_partner}",
    )


# ────────────────────── Entry point ──────────────────────


def evaluar_id_usuario_org(ticket_id: int) -> list[CriterioEvaluado]:
    """Corre las 4 sub-reglas sobre el ticket y devuelve los CriterioEvaluado."""
    try:
        ticket = zd.get_ticket(ticket_id)
    except Exception as e:
        return [
            CriterioEvaluado(
                ticket_id=ticket_id,
                criterio=CRITERIO,
                sub_regla=sr,
                resultado=Resultado.NO_EVALUABLE,
                regla=f"Error consultando ticket: {e}",
                confianza=Confianza.HEURISTICA,
            )
            for sr in ("fusion_usuario_whatsapp", "organizacion_asociada",
                       "es_partner_checkbox", "partner_id_cargado")
        ]

    try:
        comments = zd.get_ticket_comments(ticket_id, per_page=50)
    except Exception:
        comments = []

    es_partner, razon = _es_ticket_de_partner(ticket, comments)

    return [
        _evaluar_organizacion(ticket_id, ticket),
        _evaluar_fusion_usuario(ticket_id, ticket),
        _evaluar_partner_checkbox(ticket_id, ticket, es_partner, razon),
        _evaluar_partner_id(ticket_id, ticket, es_partner, razon),
    ]
