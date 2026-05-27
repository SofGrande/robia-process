"""Filtrado del catálogo de subtópicos por equipo del ticket — "estrategia del embudo".

El catálogo Doc&Comm tiene ~2243 combinaciones (Tópico Principal / Secundario /
Subtópico). Pasarlo entero al LLM es caro y ruidoso. La estrategia operativa
de Sofía (calibrada en producción a través del Clasificador de Tickets de
Zendesk) es: dado el ``team_tag`` del ticket, filtrar el catálogo a los
subtópicos del equipo y pasar solo eso al LLM.

Traducción literal a Python de ``filterGroupsByTeam`` del repo
``SofGrande/clasificador-de-tickets`` (``clasificador-AR-LT-src/assets/index.html``,
líneas 226-262). Mantiene el comportamiento JS para que cualquier ajuste
quede sincronizable entre app y evaluador.

Uso típico::

    from robia_procesos.core import equipo_mapping as em
    grupos_filtrados = em.filtrar_por_equipo(grupos, team_tag="Envio Nube")
    # grupos_filtrados[0].label = "Envíos"
    # grupos_filtrados[0].options = [Subtopico(...), ...]
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Patrón de "catch-all" subtópicos genéricos que no aportan a la clasificación
# (se filtran antes de pasar al LLM para no contaminar la decisión).
CATCHALL_PATTERN = re.compile(
    r"general|others|otros|sin clasificar|no aplica", re.IGNORECASE
)

# Longitud mínima de palabra del team_tag que consideramos "significativa"
# para match secundario contra el label del grupo.
_MIN_WORD_LEN = 4

# Valores que aparecen en la col Equipo del Sheet pero NO son equipos reales
# (headers de columna, nombres del campo, etc). Se ignoran al matchear.
_EQUIPO_NOISE = frozenset({"equipe", "equipo", "team"})


@dataclass(frozen=True)
class Subtopico:
    """Una opción del catálogo Doc&Comm, fill-down desde el Sheet maestro."""

    label: str
    equipo: str | None = None  # columna A del Sheet (puede venir vacía)
    cuando_usar: str | None = None  # guía operativa (si existe)


@dataclass(frozen=True)
class GrupoSubtopicos:
    """Grupo = Tópico Principal del Sheet maestro (col 1 en AR/LATAM)."""

    label: str
    options: tuple[Subtopico, ...]


def norm_text(s: str) -> str:
    """Normaliza: minúsculas, sin acentos, sin caracteres no alfanuméricos.

    Mismo algoritmo que ``normText`` en JS — garantiza match coherente entre
    la app de Zendesk y el evaluador Python.
    """
    if not s:
        return ""
    s = s.lower()
    # NFD descompone los caracteres acentuados (é → e + combining acute);
    # luego descartamos los combining (U+0300-U+036F).
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def norm_compact(s: str) -> str:
    """Como :func:`norm_text` pero sin espacios — para tolerar diferencias
    entre formatos "pago nube" (espaciado) y "pagonube" (pegado, formato
    custom field de Zendesk).
    """
    return norm_text(s).replace(" ", "")


def _equipo_es_noise(equipo_norm: str) -> bool:
    """True si el valor del campo Equipo del Sheet es un header (no equipo real)."""
    return equipo_norm in _EQUIPO_NOISE


def es_catchall(label: str) -> bool:
    """True si el subtópico es genérico (General X, Others, etc.)."""
    return bool(CATCHALL_PATTERN.search(label))


def filtrar_catchall(grupos: list[GrupoSubtopicos]) -> list[GrupoSubtopicos]:
    """Quita opciones catch-all de cada grupo, descarta grupos vacíos."""
    resultado: list[GrupoSubtopicos] = []
    for g in grupos:
        opts = tuple(o for o in g.options if not es_catchall(o.label))
        if opts:
            resultado.append(GrupoSubtopicos(label=g.label, options=opts))
    return resultado


def _match_equipo(team_norm: str, team_compact: str, opt_equipo: str) -> bool:
    """¿El equipo de un Subtopico matchea con el team_tag del ticket?

    Hace 2 niveles de comparación:
    1. Normalización con espacios (`norm_text`): tolera diferencias de
       puntuación y prefijos geo (``"[AR] Envío Nube"`` vs ``"Envio Nube"``).
    2. Normalización compact sin espacios (`norm_compact`): tolera Zendesk
       custom fields que pegan palabras (``"pagonube_riesgo..."`` → ``"pagonube"``)
       vs Sheet que las separa (``"Pago Nube"`` → ``"pago nube"``).

    Containment bidireccional en ambos niveles.
    Filtra valores "noise" (``"Equipe"``, ``"Equipo"``, ``"Team"`` — headers).
    """
    if not opt_equipo:
        return False
    equipo_norm = norm_text(opt_equipo)
    if _equipo_es_noise(equipo_norm):
        return False
    # Nivel 1: con espacios
    if equipo_norm == team_norm or team_norm in equipo_norm or equipo_norm in team_norm:
        return True
    # Nivel 2: sin espacios (resuelve "pagonube" vs "pago nube")
    equipo_compact = equipo_norm.replace(" ", "")
    return (
        equipo_compact == team_compact
        or team_compact in equipo_compact
        or equipo_compact in team_compact
    )


def filtrar_por_equipo(
    grupos: list[GrupoSubtopicos], team_tag: str | None
) -> list[GrupoSubtopicos] | None:
    """Filtra el catálogo a los subtópicos del equipo asignado al ticket.

    Args:
        grupos: catálogo completo (post-filtrado de catch-alls).
        team_tag: nombre del equipo del ticket. Si ``None`` o vacío, retorna ``None``.

    Returns:
        Lista de grupos filtrados. ``None`` si no hay team_tag o no hay matches
        (el caller decide si usar el catálogo completo como fallback).
    """
    if not team_tag:
        return None
    team_norm = norm_text(team_tag)
    if not team_norm:
        return None
    team_compact = team_norm.replace(" ", "")

    # Palabras significativas del nombre del equipo para match secundario en
    # label del grupo (ej. "Envio Nube" → contiene "envio" → captura grupo
    # "Post-envío" aunque no haya match en col equipo).
    team_words = [w for w in team_norm.split() if len(w) >= _MIN_WORD_LEN]

    matched: list[GrupoSubtopicos] = []
    for group in grupos:
        group_norm = norm_text(group.label)

        # Match primario: opt.equipo coincide con el team_tag.
        by_equipo = [opt for opt in group.options if _match_equipo(team_norm, team_compact, opt.equipo or "")]

        # Match secundario: si el label del grupo contiene alguna palabra
        # significativa del team_tag, traemos TODAS las opciones del grupo.
        group_matches_team = any(w in group_norm for w in team_words)
        matching = by_equipo if by_equipo else (list(group.options) if group_matches_team else [])

        if matching:
            matched.append(GrupoSubtopicos(label=group.label, options=tuple(matching)))

    return matched if matched else None
