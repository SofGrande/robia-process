"""Lista catálogos o esquemas visibles (solo lectura) para el discovery de tablas."""
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


def main() -> None:
    with sql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
    ) as conn:
        with conn.cursor() as cur:
            # Unity Catalog: catálogos
            try:
                cur.execute("SHOW CATALOGS")
                print("== SHOW CATALOGS ==")
                for row in cur.fetchall():
                    print(row)
            except Exception as e:  # noqa: BLE001
                print("SHOW CATALOGS no disponible:", e, file=sys.stderr)
            # Esquemas (primeros 50) en el catálogo activo
            try:
                cur.execute(
                    "SELECT catalog_name, schema_name FROM information_schema.schemata "
                    "ORDER BY catalog_name, schema_name LIMIT 80"
                )
                print("\n== information_schema.schemata (hasta 80) ==")
                for row in cur.fetchall():
                    print(row)
            except Exception as e:  # noqa: BLE001
                print("information_schema no accesible:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
