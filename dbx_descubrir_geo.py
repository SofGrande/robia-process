"""Descubrir cómo está modelada la geografía (AR / BR / MX) en data_cx.

Recorre tablas candidatas con DESCRIBE para ver columnas. Después prueba
SELECT DISTINCT en columnas sospechosas.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from databricks import sql
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / "Credenciales" / ".env")

CATALOG = "data_products_prd"
SCHEMA = "data_cx"
FQN = f"`{CATALOG}`.`{SCHEMA}`"

CANDIDATAS = [
    "s__general__zendesk_assignment__event",
    "s__tech__ticket_subdomains__event",
    "s__tech__ticket_custom_fields__event",
    "s__general__zendesk_ticket_topics__event",
    "s__general__zendesk_interactions__event",
]

OUT = _ROOT / "_geo_descubrir.txt"


def main() -> int:
    host = os.environ["DATABRICKS_SERVER_HOSTNAME"]
    http_path = os.environ["DATABRICKS_HTTP_PATH"]
    token = os.environ.get("databricks_token") or os.environ["DATABRICKS_TOKEN"]
    lines: list[str] = []
    with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as conn:
        with conn.cursor() as cur:
            for tabla in CANDIDATAS:
                lines.append(f"=== {tabla} ===")
                try:
                    cur.execute(f"DESCRIBE {FQN}.`{tabla}`")
                    cols = cur.fetchall() or []
                    for c in cols:
                        # cada fila típicamente: (col_name, data_type, comment)
                        if c and c[0]:
                            nombre = str(c[0])
                            tipo = str(c[1]) if len(c) > 1 else ""
                            lines.append(f"  {nombre:<32} {tipo}")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"  (error: {e!s})")
                lines.append("")

            # Probar valores distintos en columnas que suelen tener pista de geo
            sospechosas = [
                ("s__general__zendesk_assignment__event", "group_id"),
                ("s__general__zendesk_assignment__event", "guru_name"),
                ("s__tech__ticket_custom_fields__event", "field_name"),
            ]
            for tabla, col in sospechosas:
                lines.append(f"--- DISTINCT {col} en {tabla} (top 30) ---")
                try:
                    cur.execute(
                        f"SELECT DISTINCT CAST({col} AS STRING) AS v FROM {FQN}.`{tabla}` "
                        f"WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 30"
                    )
                    vals = [str(r[0]) for r in (cur.fetchall() or [])]
                    for v in vals:
                        lines.append(f"  {v}")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"  (error: {e!s})")
                lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK -> {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
