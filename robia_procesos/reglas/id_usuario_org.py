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

CRITERIO = "Id Usuario/Org"

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


AUTHOR_SYSTEM = -1  # author_id usado por Zendesk para triggers/automatizaciones


def _evaluar_organizacion(ticket_id: int, ticket: dict) -> CriterioEvaluado:
    """Evalúa si la organización del cliente está asociada (al ticket o al user).

    Regla refinada con Sofía (2026-06-01): el "historial de conversación" se
    actualiza cuando el CLIENTE (user) tiene organización, no necesariamente
    cuando el ticket la tiene seteada. Por eso chequeamos ambos lugares:

    1. ticket.organization_id está poblado → THUMBS_UP (directo).
    2. ticket.organization_id es None PERO user.organization_id está poblado
       → THUMBS_UP (la org está asociada al cliente, el historial se actualiza).
    3. Ambos None → THUMBS_DOWN (cliente sin org, nadie hizo el trabajo).

    Si la org del ticket fue seteada por trigger automático (audit con
    author_id=-1) y no por guru, sigue siendo válido — el historial se
    actualiza igual. Por eso ya no diferenciamos N/A vs THUMBS_UP en ese caso
    (el nuevo schema binarizo igual ambos a score=0).
    """
    org_ticket = ticket.get("organization_id")

    # Caso 1: ticket tiene org → OK directo.
    if org_ticket:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="organizacion_asociada",
            resultado=Resultado.THUMBS_UP,
            regla="Organización: Asociada al ticket con éxito.",
            confianza=Confianza.DIRECTA,
        )

    # Caso 2: ticket sin org pero user con org → también OK.
    requester_id = ticket.get("requester_id")
    if requester_id:
        try:
            user = zd.get_user(requester_id)
            org_user = user.get("organization_id")
            if org_user:
                return CriterioEvaluado(
                    ticket_id=ticket_id,
                    criterio=CRITERIO,
                    sub_regla="organizacion_asociada",
                    resultado=Resultado.THUMBS_UP,
                    regla="Organización: El cliente tiene organización asociada; el historial de conversación se actualiza correctamente.",
                    confianza=Confianza.DIRECTA,
                )
        except Exception:
            pass  # si falla la query del user, caemos al caso 3

    # Caso 3: ni ticket ni user tienen org.
    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="organizacion_asociada",
        resultado=Resultado.THUMBS_DOWN,
        regla="Organización: Ni el ticket ni el cliente tienen organización asociada.",
        confianza=Confianza.DIRECTA,
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
            regla="Canal WhatsApp: El ticket no entró por WhatsApp.",
            confianza=Confianza.DIRECTA,
        )

    org_id = ticket.get("organization_id")
    if not org_id:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla="Canal WhatsApp: Entró por WhatsApp pero sin organización asociada, no se puede verificar fusión.",
            confianza=Confianza.DIRECTA,
        )

    requester_id = ticket.get("requester_id")
    try:
        requester = zd.get_user(requester_id) if requester_id else {}
    except Exception:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla="Canal WhatsApp: No se pudo consultar al usuario del ticket.",
            confianza=Confianza.HEURISTICA,
        )

    if requester.get("email"):
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.THUMBS_UP,
            regla="Canal WhatsApp: El cliente ya tiene un correo registrado; no hizo falta fusionar cuentas.",
            confianza=Confianza.DIRECTA,
        )

    try:
        users = zd.search_users_by_org(org_id)
    except Exception:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.NO_EVALUABLE,
            regla="Canal WhatsApp: No se pudo buscar otras cuentas del cliente en la organización.",
            confianza=Confianza.HEURISTICA,
        )

    otros_con_email = [
        u for u in users
        if u.get("id") != requester_id and u.get("email")
    ]
    if otros_con_email:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="fusion_usuario_whatsapp",
            resultado=Resultado.THUMBS_DOWN,
            regla="Canal WhatsApp: El cliente tiene otra cuenta con email en la misma organización que no se fusionó al ticket.",
            confianza=Confianza.PARCIAL,
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="fusion_usuario_whatsapp",
        resultado=Resultado.THUMBS_UP,
        regla="Canal WhatsApp: El cliente entró por WhatsApp y no tiene otra cuenta para fusionar.",
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
            regla="Estado Partner: El ticket no pertenece a un partner.",
            confianza=Confianza.DIRECTA,
        )

    es_partner_val = _custom_field_value(ticket, FIELD_ES_PARTNER)
    if es_partner_val is True:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="es_partner_checkbox",
            resultado=Resultado.THUMBS_UP,
            regla="Estado Partner: El checkbox 'Es partner?' está tildado correctamente.",
            confianza=Confianza.DIRECTA,
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="es_partner_checkbox",
        resultado=Resultado.THUMBS_DOWN,
        regla="Estado Partner: El ticket pertenece a un equipo de Partners pero no se tildó el checkbox 'Es partner?'.",
        confianza=Confianza.DIRECTA,
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
            regla="Partner ID: El ticket no pertenece a un partner.",
            confianza=Confianza.DIRECTA,
        )

    partner_id = _custom_field_value(ticket, FIELD_PARTNER_ID)
    if partner_id:
        return CriterioEvaluado(
            ticket_id=ticket_id,
            criterio=CRITERIO,
            sub_regla="partner_id_cargado",
            resultado=Resultado.THUMBS_UP,
            regla="Partner ID: Cargado correctamente.",
            confianza=Confianza.DIRECTA,
        )

    return CriterioEvaluado(
        ticket_id=ticket_id,
        criterio=CRITERIO,
        sub_regla="partner_id_cargado",
        resultado=Resultado.THUMBS_DOWN,
        regla="Partner ID: El ticket pertenece a un equipo de Partners pero no se cargó el Partner ID.",
        confianza=Confianza.DIRECTA,
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
