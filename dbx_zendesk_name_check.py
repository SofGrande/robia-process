import os
from pathlib import Path
from dotenv import load_dotenv
from databricks import sql

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / "Credenciales" / ".env")
host = os.environ["DATABRICKS_SERVER_HOSTNAME"]
http_path = os.environ["DATABRICKS_HTTP_PATH"]
token = os.environ.get("databricks_token") or os.environ["DATABRICKS_TOKEN"]

Q = """
SELECT table_catalog, table_schema, table_name
FROM `data_products_prd`.information_schema.tables
WHERE table_type IN ('BASE TABLE', 'VIEW')
  AND LOWER(table_name) LIKE '%zend%'
ORDER BY table_schema, table_name
LIMIT 200
"""

with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as conn:
    with conn.cursor() as cur:
        cur.execute(Q)
        for r in cur.fetchall() or []:
            print(f"{r[0]}\t{r[1]}\t{r[2]}")
