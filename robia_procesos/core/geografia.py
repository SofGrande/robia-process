"""Detección de geografía del ticket — usado para validar contra el catálogo
correcto en las reglas de clasificación.

Tiendanube (LATAM, marca AR/MX) y Nuvemshop (BR) son las dos marcas regionales.
Hoy la fuente más directa en el lake es ``guru_name`` en
``s__general__zendesk_assignment__event``: los nombres siguen el patrón
``Nombre de Tiendanube`` (LATAM) o ``Nombre da Nuvemshop`` (BR). Hay nombres
legacy sin patrón (p.ej. "Adrian", "Ale") que quedan como ``DESCONOCIDA``.

Para tickets multi-asignados, tomamos el guru de la **última asignación**
(criterio: la geo final del ticket es la que cuenta). En la práctica los
tickets no cambian de marca en su ciclo de vida, pero el último guru es la
señal más limpia.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable

from robia_procesos.core import db


class Geografia(str, Enum):
    LATAM = "LATAM"          # Tiendanube AR / MX / LATAM
    BR = "BR"                # Nuvemshop BR
    DESCONOCIDA = "desconocida"


# Hojas del catálogo maestro que aplican a cada geografía.
# LATAM combina la hoja LATAM (genérica) con AR (Argentina específica).
# Si más adelante hay catálogo MX, se agrega acá.
HOJAS_POR_GEO: dict[Geografia, tuple[str, ...]] = {
    Geografia.LATAM: ("AR_Tópicos_Zendesk/Slack", "LATAM_Tópicos_Zendesk/Slack"),
    Geografia.BR: ("[BR] Tópicos Zendesk/Slack",),
    Geografia.DESCONOCIDA: (),  # vacía → caller decide qué hacer
}


def parsear_guru_name(name: str | None) -> Geografia:
    """Mapear ``guru_name`` a geografía. Insensible a mayúsculas en el sufijo.

    >>> parsear_guru_name("Dagmara de Tiendanube")
    <Geografia.LATAM: 'LATAM'>
    >>> parsear_guru_name("Adauto da Nuvemshop")
    <Geografia.BR: 'BR'>
    >>> parsear_guru_name("Agente Virtual AR")
    <Geografia.LATAM: 'LATAM'>
    >>> parsear_guru_name("Adrian")
    <Geografia.DESCONOCIDA: 'desconocida'>
    """
    if not name:
        return Geografia.DESCONOCIDA
    n = name.strip().lower()
    if "da nuvemshop" in n or "agente virtual br" in n or "nuvemshop" in n:
        return Geografia.BR
    if "de tiendanube" in n or "agente virtual ar" in n or "tiendanube" in n:
        return Geografia.LATAM
    return Geografia.DESCONOCIDA


def detectar_geografia(ticket_ids: Iterable[int]) -> dict[int, Geografia]:
    """Devuelve {ticket_id: Geografia} usando el guru del último assignment."""
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
    return {int(r[0]): parsear_guru_name(r[1]) for r in rows}
