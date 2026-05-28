"""Escritor de filas al Sheet de auditorías de RobIA Procesos.

El service account ``robia-qa-pipeline@support-468213.iam.gserviceaccount.com``
tiene permiso de Editor en el Sheet
``1UaJCklivyvrfOlD9OvQ7RkoyGHkR0VWj1rdbtoPj9BU``, worksheet
``[AR] RobIA Procesos``.

Convención:
    - Header en fila 1 (ya existe en el Sheet, no se toca).
    - Filas de datos a partir de la fila 2.
    - **Idempotencia por ticket_id (col C):** si un ticket ya tiene filas
      en el Sheet, las borramos antes de escribir las nuevas. Esto permite
      re-correr el evaluador sin duplicar.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from robia_procesos.core.output import FilaOutput

SHEET_ID = "1UaJCklivyvrfOlD9OvQ7RkoyGHkR0VWj1rdbtoPj9BU"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Mapping país → worksheet en el mismo Sheet.
WORKSHEETS_POR_PAIS: dict[str, str] = {
    "AR": "[AR] RobIA Procesos",
    "BR": "[BR] RobIA Procesos",
    "LT": "[LT] RobIA Procesos",
}

_ROOT = Path(__file__).resolve().parents[2]
CREDS_PATH = _ROOT / "Credenciales" / "service_account_robia.json"


@lru_cache(maxsize=4)
def _worksheet(nombre: str) -> gspread.Worksheet:
    """Abre el worksheet por nombre — cacheado para no re-autenticar."""
    creds = Credentials.from_service_account_file(str(CREDS_PATH), scopes=SCOPES)
    sh = gspread.authorize(creds).open_by_key(SHEET_ID)
    return sh.worksheet(nombre)


def worksheet_para_pais(pais: str) -> str:
    """AR → '[AR] RobIA Procesos', etc. Falla claro si no está mapeado."""
    try:
        return WORKSHEETS_POR_PAIS[pais.upper()]
    except KeyError:
        raise ValueError(
            f"País desconocido: {pais!r}. Soportados: {list(WORKSHEETS_POR_PAIS)}"
        )


def _tickets_existentes(ws: gspread.Worksheet) -> dict[int, list[int]]:
    """Lee la col C y devuelve {ticket_id: [filas_1indexadas]}.

    Filas vacías o no numéricas se ignoran. La fila 1 es header — se salta.
    """
    valores = ws.col_values(3)  # col C "Nº do Ticket"
    out: dict[int, list[int]] = {}
    for i, v in enumerate(valores[1:], start=2):  # empezamos desde fila 2
        v = (v or "").strip()
        if not v:
            continue
        try:
            tid = int(v)
        except ValueError:
            continue
        out.setdefault(tid, []).append(i)
    return out


def escribir_filas(
    filas: list[FilaOutput],
    reemplazar_existentes: bool = True,
    worksheet_name: str = "[AR] RobIA Procesos",
) -> dict[str, int]:
    """Escribe filas al worksheet indicado.

    Args:
        filas: lista de FilaOutput. Pueden ser de varios tickets.
        reemplazar_existentes: si True, borra filas previas de los mismos
            ticket_ids antes de escribir las nuevas. Si False, agrega al final.
        worksheet_name: nombre del worksheet destino. Default AR.

    Returns:
        {"escritas": N, "eliminadas_previas": M}
    """
    ws = _worksheet(worksheet_name)
    if not filas:
        return {"escritas": 0, "eliminadas_previas": 0}

    eliminadas = 0
    if reemplazar_existentes:
        tickets_nuevos = {f.ticket_id for f in filas}
        existentes = _tickets_existentes(ws)
        # Recolectar filas a borrar (orden descendente para que los índices
        # no se corran al borrar).
        filas_a_borrar: list[int] = []
        for tid in tickets_nuevos:
            filas_a_borrar.extend(existentes.get(tid, []))
        for row_idx in sorted(filas_a_borrar, reverse=True):
            ws.delete_rows(row_idx)
            eliminadas += 1

    # Append en batch — gspread acepta una matriz.
    matriz = [f.to_row() for f in filas]
    ws.append_rows(matriz, value_input_option="USER_ENTERED")
    return {"escritas": len(filas), "eliminadas_previas": eliminadas}
