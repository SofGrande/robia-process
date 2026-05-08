"""Conexión de prueba a Databricks SQL (warehouse). Carga secretos solo desde Credenciales/.env."""
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

if not all([host, http_path, token]):
    print("Faltan variables: DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, databricks_token", file=sys.stderr)
    sys.exit(1)

def main() -> None:
    with sql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            rows = cur.fetchall()
    print("Conexión OK. Resultado de prueba:", rows)


if __name__ == "__main__":
    main()
