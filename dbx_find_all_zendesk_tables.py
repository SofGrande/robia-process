"""
Lista tablas cuyo nombre contiene 'zend' en todos los catálogos con information_schema accesible.
Solo metadatos (catálogo, esquema, nombre de tabla).
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from databricks import sql

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / "Credenciales" / ".env")

host = os.environ.get("DATABRICKS_SERVER_HOSTNAME")
http_path = os.environ.get("DATABRICKS_HTTP_PATH")
token = os.environ.get("databricks_token") or os.environ.get("DATABRICKS_TOKEN")


def main() -> int:
    with sql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW CATALOGS")
            cats = [r[0] for r in (cur.fetchall() or []) if r and r[0]]
            if "system" in cats:
                cats = [c for c in cats if c not in ("system", "samples")]

    total = []
    for cat in cats:
        with sql.connect(
            server_hostname=host,
            http_path=http_path,
            access_token=token,
        ) as conn:
            with conn.cursor() as cur:
                try:
                    q = f"""
                    SELECT table_catalog, table_schema, table_name
                    FROM `{cat}`.information_schema.tables
                    WHERE table_type IN ('BASE TABLE', 'VIEW')
                      AND (
                        LOWER(table_name) LIKE '%zend%'
                        OR LOWER(table_name) LIKE '%zen_desk%'
                      )
                    ORDER BY table_schema, table_name
                    """
                    cur.execute(q)
                    rows = cur.fetchall() or []
                except Exception as e:  # noqa: BLE001
                    print(f"# {cat}: (sin acceso a information_schema) {e!s}", file=sys.stderr)
                    continue
                for r in rows:
                    total.append((str(r[0]), str(r[1]), str(r[2])))

    # también: nombre 'ticket' muy genérico — opcional, comentado para no spamear
    print("Total tablas/vistas con 'zend' en el nombre (por information_schema):")
    print("catalog | schema | table")
    print("--------|--------|------")
    for a, b, c in sorted(set(total), key=lambda x: (x[0], x[1], x[2])):
        print(f"{a} | {b} | {c}")
    print(f"\n== Total: {len(set(total))} tablas/vistas ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
