"""Cliente Databricks — un solo lugar donde vivan host, http_path y token.

Reusa el patrón de los scripts `dbx_*.py`: lee `Credenciales/.env` desde la raíz
del proyecto. Acepta tanto `DATABRICKS_TOKEN` como la variante minúscula
`databricks_token` que aparece en el .env local de Sofía.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from databricks import sql
from dotenv import load_dotenv

CATALOG = "data_products_prd"
SCHEMA = "data_cx"
FQN = f"`{CATALOG}`.`{SCHEMA}`"

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / "Credenciales" / ".env")


def _credenciales() -> tuple[str, str, str]:
    host = os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")
    token = os.environ.get("databricks_token") or os.environ.get("DATABRICKS_TOKEN")
    if not all([host, http_path, token]):
        raise RuntimeError(
            "Faltan credenciales Databricks. Definir DATABRICKS_SERVER_HOSTNAME, "
            "DATABRICKS_HTTP_PATH y (databricks_token | DATABRICKS_TOKEN) en "
            "Credenciales/.env"
        )
    return host, http_path, token  # type: ignore[return-value]


@contextmanager
def cursor() -> Iterator[Any]:
    """Yield a Databricks cursor; cierra conn/cursor al salir."""
    host, http_path, token = _credenciales()
    with sql.connect(
        server_hostname=host, http_path=http_path, access_token=token
    ) as conn:
        with conn.cursor() as cur:
            yield cur


def fetch(query: str, params: dict[str, Any] | None = None) -> list[tuple]:
    """Ejecutar query y devolver filas. `params` se inyecta con `%(name)s`."""
    with cursor() as cur:
        cur.execute(query, params or {})
        return cur.fetchall() or []
