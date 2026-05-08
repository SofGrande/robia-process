"""
Exploración: localizar esquemas/tabl Zendesk y probar 5 ticket IDs.
Solo nombres de columnas mínimas en stdout; conteos, no cuerpos de conversación.
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from databricks import sql

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / "Credenciales" / ".env")

host = os.environ.get("DATABRICKS_SERVER_HOSTNAME")
http_path = os.environ.get("DATABRICKS_HTTP_PATH")
token = os.environ.get("databricks_token") or os.environ.get("DATABRICKS_TOKEN")

TICKET_IDS = [7_189_367, 7_253_209, 7_270_214, 7_242_316, 7_243_898]
# Catálogos a inspeccionar (prioritarios; ampliar si hace falta)
CATALOGS = ["raw", "data_products_prd", "data_products_dev", "bronze_risk", "ds_catalog"]


def run(cur, q: str):
    cur.execute(q)
    return cur.fetchall()


def main() -> int:
    id_list = ", ".join(str(t) for t in TICKET_IDS)
    with sql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
    ) as conn:
        with conn.cursor() as cur:
            # 1) tablas cuyo nombre sugiere Zendesk / tickets
            for cat in CATALOGS:
                try:
                    q = f"""
                    SELECT table_schema, table_name
                    FROM `{cat}`.information_schema.tables
                    WHERE table_type = 'BASE TABLE'
                      AND (
                        lower(table_name) LIKE '%zend%'
                        OR lower(table_name) LIKE '%zen_desk%'
                        OR (lower(table_name) LIKE '%ticket%'
                            AND (lower(table_name) LIKE '%event%'
                                 OR lower(table_name) LIKE '%comment%'
                                 OR lower(table_name) LIKE '%audit%'))
                      )
                    ORDER BY table_schema, table_name
                    """
                    rows = run(cur, q)
                except Exception as e:  # noqa: BLE001
                    print(f"--- {cat}.information_schema.tables: no accesible\n    {e}\n", file=sys.stderr)
                    continue
                if rows:
                    print(f"=== {cat} — tablas candidatas (nombre) ({len(rows)}) ===")
                    for r in rows[:200]:
                        print(f"  {r[0]}.{r[1]}")

            # 2) columnas 'ticket' en esquemas frecuentes (muestreo por catálogo)
            for cat in CATALOGS:
                try:
                    q = f"""
                    SELECT table_schema, table_name, column_name, data_type
                    FROM `{cat}`.information_schema.columns
                    WHERE (
                        lower(column_name) = 'id'
                        OR lower(column_name) = 'ticket_id'
                        OR lower(column_name) LIKE 'ticket%'
                    )
                    AND table_schema NOT IN ('information_schema')
                    ORDER BY table_schema, table_name, column_name
                    """
                    rows = run(cur, q)
                except Exception as e:  # noqa: BLE001
                    print(f"--- {cat}.columns: {e}\n", file=sys.stderr)
                    continue
                if not rows:
                    continue
                print(f"\n=== {cat} — columnas con ticket/id relevantes (primeras 100 filas) ===")
                for r in rows[:100]:
                    print(f"  {r[0]}.{r[1]}  {r[2]} ({r[3]})")

            # 3) búsqueda tosca: tablas con columna numérica (ticket id) y nombre hint zendesk
            print(
                "\n--- Si conocés `catalogo.esquema.tabla` y col `ticket_id`, añadí a BUSCAR abajo. "
                f"IDs de prueba: {id_list} ---\n"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
