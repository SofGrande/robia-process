"""
Exporta listado de eventos en data_products_prd.data_cx:
- data_cx_tablas_evento.csv: todas las tablas * __event
- data_cx_eventos_detalle.csv: valores distintos por columna discriminadora
"""
import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from databricks import sql

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / "Credenciales" / ".env")

CATALOG = "data_products_prd"
SCHEMA = "data_cx"
FQN = f"`{CATALOG}`.{SCHEMA}"

host = os.environ["DATABRICKS_SERVER_HOSTNAME"]
http_path = os.environ["DATABRICKS_HTTP_PATH"]
token = os.environ.get("databricks_token") or os.environ["DATABRICKS_TOKEN"]

# (tabla, columna NULLABLE para DISTINCT, descripcion breve)
DISCRIMINATORS: list[tuple[str, str | None, str]] = [
    ("s__general__zendesk_tickets_events__event", "field_name", "Cambios de campo (Zendesk ticket)"),
    ("s__general__zendesk_interactions__event", "interaction_type", "Tipo de interacción (hilo)"),
    ("s__general__zendesk_interactions__event", "source", "Origen de la interacción"),
    ("s__general__zendesk_assignment__event", "assignment_type", "Tipo de asignación"),
    (
        "s__general__zendesk_ticket_nature__event",
        "general_nature",
        "Naturaleza (IQS)",
    ),
    (
        "s__general__zendesk_ticket_topics__event",
        "main_topic_normalized",
        "Tópico principal normalizado",
    ),
    (
        "s__general__zendesk_ticket_topics__event",
        "subtopic_normalized",
        "Subtópico normalizado",
    ),
    (
        "s__general__zendesk_ticket_topics__event",
        "secondary_topic_normalized",
        "Tópico secundario",
    ),
    ("s__tech__ticket_custom_fields__event", "field_name", "Custom field (tabla tech)"),
    (
        "s__tech__ticket_issue_problem__event",
        "issue_problem_type",
        "Tipo issue/problem",
    ),
    ("s__tech__ticket_subdomains__event", "domain", "Dominio (subdominios)"),
    ("s__human__csat__event", "csat_event_type", "Tipo evento CSAT humano"),
    (
        "s__general__zendesk_macros_usage__event",
        "macro_id",
        "Id de macro (si aplica; puede ser null en algunos lados)",
    ),
    (
        "s__general__zendesk_chats__event",
        "actor_type",
        "Tipo de actor en evento de chat",
    ),
    (
        "s__general__zendesk_satisfaction_score__event",
        "satisfaction_score",
        "Valor numérico/estado de satisfacción (distinto de CSAT humano)",
    ),
]


def run_distinct(cur, table: str, col: str) -> list[str]:
    t = f"{FQN}.`{table}`"
    q = f"SELECT DISTINCT CAST({col} AS STRING) AS v FROM {t} WHERE {col} IS NOT NULL ORDER BY 1"
    cur.execute(q)
    return [r[0] for r in (cur.fetchall() or []) if r and r[0] is not None]


def list_event_tables(cur) -> list[str]:
    cur.execute(f"SHOW TABLES IN {FQN}")
    rows = cur.fetchall() or []
    out: list[str] = []
    for r in rows:
        if not r or len(r) < 1:
            continue
        tname = r[1] if len(r) > 1 and r[1] else r[0]
        if not tname or not str(tname).endswith("__event"):
            continue
        out.append(str(tname))
    return sorted(out)


def main() -> int:
    out_dir = _ROOT
    with sql.connect(
        server_hostname=host, http_path=http_path, access_token=token
    ) as conn:
        with conn.cursor() as cur:
            tables = list_event_tables(cur)
            if not tables:
                print("No se encontraron tablas *__event", file=sys.stderr)
                return 1

            # 1) tablas
            path_idx = out_dir / "data_cx_tablas_evento.csv"
            with path_idx.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(
                    [
                        "orden",
                        "catalogo",
                        "esquema",
                        "tabla",
                        "nombre_legible",
                    ]
                )
                for i, t in enumerate(tables, start=1):
                    leg = t.replace("__", " / ").replace("_", " ") if t else ""
                    w.writerow([i, CATALOG, SCHEMA, t, leg])

            # 2) detalle
            path_det = out_dir / "data_cx_eventos_detalle.csv"
            done_pairs: set[tuple[str, str]] = set()
            with path_det.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(
                    [
                        "tabla_evento",
                        "columna",
                        "valor",
                        "descripcion",
                        "nota",
                    ]
                )

                for table, col, desc in DISCRIMINATORS:
                    if table not in tables:
                        w.writerow(
                            [
                                table,
                                col or "",
                                "",
                                desc,
                                "no existe en esquema actual",
                            ]
                        )
                        continue
                    if col is None:
                        w.writerow(
                            [
                                table,
                                "(describe)",
                                "",
                                desc,
                                "sin columna categórica fija; ver UI Databricks",
                            ]
                        )
                        continue
                    pair = (table, col)
                    if pair in done_pairs:
                        continue
                    done_pairs.add(pair)
                    try:
                        vals = run_distinct(cur, table, col)
                    except Exception as e:  # noqa: BLE001
                        w.writerow(
                            [
                                table,
                                col,
                                "",
                                desc,
                                f"error: {e!s}",
                            ]
                        )
                        continue
                    for v in vals:
                        w.writerow([table, col, v, desc, ""])
                    if not vals:
                        w.writerow(
                            [
                                table,
                                col,
                                "(sin datos no nulos)",
                                desc,
                                "",
                            ]
                        )

                # tablas __event no cubiertas
                for t in tables:
                    if t not in {a for a, _, _ in DISCRIMINATORS}:
                        w.writerow(
                            [
                                t,
                                "",
                                "",
                                "Tabla de evento adicional: revisar columnas (DESCRIBE) en Databricks",
                                "no listada en mapeo DISCRIMINATORS de este script",
                            ]
                        )

    print("OK ->", path_idx.name, "y", path_det.name, "en", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
