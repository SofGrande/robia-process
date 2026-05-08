"""Descubrir estructura del Google Sheet de Tópicos / Naturaleza.

Lee las 5 hojas declaradas por Sofía y vuelca un JSON con la estructura cruda
y un resumen, evitando UnicodeEncodeError de la consola Windows. El catálogo
real lo construimos a partir de este dump.
"""
from __future__ import annotations

import json
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1OToNB4aEe5n5ciD--NngBCrUkWCdr6AoH_NyDZ2p63Y"
HOJAS = [
    "AR_Tópicos_Zendesk/Slack",
    "LATAM_Tópicos_Zendesk/Slack",
    "[BR] Tópicos Zendesk/Slack",
    "[LATAM] Naturaleza da Conversa",
    "[BR] Natureza da Conversa",
    "[AR] Ruteo Nube y TS",
    "[BR] Ruteo Nube e TS",
]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

_ROOT = Path(__file__).resolve().parent
SA_PATH = _ROOT / "Credenciales" / "service_account_robia.json"
OUT_JSON = _ROOT / "_topicos_raw_dump.json"
OUT_RESUMEN = _ROOT / "_topicos_resumen.txt"


def main() -> int:
    creds = Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    dump: dict[str, list[list[str]]] = {}
    nombres_existentes = [w.title for w in sh.worksheets()]
    dump["__nombres_hojas_detectadas__"] = [[n] for n in nombres_existentes]

    for hoja_nombre in HOJAS:
        if hoja_nombre not in nombres_existentes:
            dump[hoja_nombre] = [["(NO ENCONTRADA)"]]
            continue
        ws = sh.worksheet(hoja_nombre)
        valores = ws.get_all_values()
        dump[hoja_nombre] = valores

    OUT_JSON.write_text(
        json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Resumen ASCII-safe (sin contenido literal)
    lines = [f"Sheet: {sh.title}"]
    lines.append("")
    lines.append("Hojas detectadas:")
    for n in nombres_existentes:
        lines.append(f"  - {n}")
    lines.append("")
    for hoja_nombre in HOJAS:
        rows = dump.get(hoja_nombre, [])
        lines.append(f"=== {hoja_nombre} ===")
        if rows and rows[0] == ["(NO ENCONTRADA)"]:
            lines.append("  (no encontrada)")
        else:
            lines.append(
                f"  filas={len(rows)} | columnas={(len(rows[0]) if rows else 0)}"
            )
        lines.append("")

    OUT_RESUMEN.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK -> {OUT_JSON.name} y {OUT_RESUMEN.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
