import os
from pathlib import Path
from dotenv import load_dotenv
from databricks import sql

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / "Credenciales" / ".env")
h = os.environ["DATABRICKS_SERVER_HOSTNAME"]
p = os.environ["DATABRICKS_HTTP_PATH"]
t = os.environ.get("databricks_token") or os.environ["DATABRICKS_TOKEN"]

with sql.connect(server_hostname=h, http_path=p, access_token=t) as conn:
    with conn.cursor() as cur:
        for label, q in [
            ("information_schema data_cx", "SELECT table_name FROM `data_products_prd`.information_schema.tables WHERE table_schema = 'data_cx' ORDER BY table_name LIMIT 50"),
            ("SHOW TABLES", "SHOW TABLES IN `data_products_prd`.data_cx"),
        ]:
            print("===", label, "===")
            try:
                cur.execute(q)
                rows = cur.fetchall()
                for r in (rows or [])[:50]:
                    print(r)
            except Exception as e:  # noqa: BLE001
                print("ERR", e)
            print()
