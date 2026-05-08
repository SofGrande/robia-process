"""
Prueba de tablas `data_cx` (eventos Zendesk / CX).
- Resuelve catálogo si existe `data_cx` en el warehouse.
- DESCRIBE de tablas clave y conteos por ticket_id (sin dump de mensajes).
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

SCHEMA = "data_cx"
CATALOGS_TRY = [
    "data_products_prd",
    "data_products_dev",
    "raw",
    "ds_catalog",
]

TICKET_IDS = (7_189_367, 7_253_209, 7_270_214, 7_242_316, 7_243_898)

TABLES_OF_INTEREST = [
    "s__general__zendesk_tickets_events__event",
    "s__general__zendesk_interactions__event",
    "s__general__zendesk_ticket_topics__event",
    "s__general__zendesk_ticket_nature__event",
    "s__general__zendesk_assignment__event",
    "s__tech__ticket_custom_fields__event",
    "s__tech__ticket_issue_problem__event",
    "s__general__zendesk_chats__event",
    "s__general__zendesk_satisfaction_score__event",
    "s__general__zendesk_sla_target__event",
    "s__tech__ticket_subdomains__event",
    "s__human__csat__event",
]


def fetchall(cur, q: str):
    cur.execute(q)
    return cur.fetchall()


def find_catalog_with_schema(cur) -> str | None:
    for cat in CATALOGS_TRY:
        try:
            rows = fetchall(cur, f"SHOW SCHEMAS IN `{cat}`")
        except Exception:  # noqa: BLE001
            continue
        for r in rows or []:
            for part in r:
                if str(part).lower() == SCHEMA:
                    return cat
    return None


def col_names_from_describe(rows) -> list[str]:
    out = []
    for r in rows:
        if r and r[0]:
            out.append(str(r[0]).lower())
    return out


def pick_ticket_predicate(cols: list[str]) -> str | None:
    for c in ("ticket_id", "zendesk_ticket_id", "external_id", "id_ticket"):
        if c in cols:
            return c
    return None


def main() -> int:
    id_in = ", ".join(str(t) for t in TICKET_IDS)
    with sql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
    ) as conn:
        with conn.cursor() as cur:
            cat = os.environ.get("DATA_CX_CATALOG", "").strip() or None
            if not cat:
                cat = find_catalog_with_schema(cur)
            if not cat:
                print(
                    "No se encontró el esquema `data_cx` en catálogos: "
                    + ", ".join(CATALOGS_TRY),
                    file=sys.stderr,
                )
                print(
                    "Definí CATALOGO manual: set DATA_CX_CATALOG=nombre en .env o variable de entorno.",
                    file=sys.stderr,
                )
                cat = os.environ.get("DATA_CX_CATALOG", "").strip()
                if not cat:
                    return 1

            fqn = f"`{cat}`.{SCHEMA}"
            print(f"Usando: {cat}.{SCHEMA}\n")

            for t in TABLES_OF_INTEREST:
                full = f"{fqn}.`{t}`"
                try:
                    drows = fetchall(cur, f"DESCRIBE TABLE {full}")
                except Exception as e:  # noqa: BLE001
                    print(f"--- {t}: omitida ({e})")
                    continue
                cols = col_names_from_describe(drows)
                pred = pick_ticket_predicate(cols)
                print(f"=== {t} ===")
                print("  columnas (primeras 15):", ", ".join(cols[:15]), "..." if len(cols) > 15 else "")

                if not pred:
                    # payload JSON: intentar contar filas totales (acotar)
                    if any("data" in c or "body" in c or "event" in c for c in cols):
                        print("  nota: posible JSON anidado; no hay ticket_id al primer nivel. Revisar con expertos.\n")
                    else:
                        print("  no se detectó columna ticket_id / id clara; revisar describe manual.\n")
                    continue

                try:
                    q = f"""
                    SELECT {pred} AS k, count(*) AS n
                    FROM {full}
                    WHERE {pred} IN ({id_in})
                    GROUP BY {pred} ORDER BY k
                    """
                    summary = fetchall(cur, q)
                except Exception as e:  # noqa: BLE001
                    print(f"  conteo: error ({e})\n")
                    continue
                if summary:
                    for row in summary:
                        print(f"  tickets {row[0]}: {row[1]} filas (evento)")
                else:
                    print("  0 filas para los 5 ticket IDs en este slice.")
                print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
