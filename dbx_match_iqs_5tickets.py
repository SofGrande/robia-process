"""
Cruce: criterios IQS (alto nivel) vs `data_products_prd.data_cx` — 5 tickets de ejemplo.
Salida: match_iqs_criterios_5tickets_ejemplo.csv
"""
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from databricks import sql

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / "Credenciales" / ".env")

CATALOG = "data_products_prd"
SCHEMA = "data_cx"
FQN = f"`{CATALOG}`.`{SCHEMA}`"
TICKETS = (7_189_367, 7_253_209, 7_270_214, 7_242_316, 7_243_898)
T_IN = ", ".join(str(t) for t in TICKETS)

OUT_CSV = _ROOT / "match_iqs_criterios_5tickets_ejemplo.csv"

host = os.environ.get("DATABRICKS_SERVER_HOSTNAME")
http_path = os.environ.get("DATABRICKS_HTTP_PATH")
token = os.environ.get("databricks_token") or os.environ.get("DATABRICKS_TOKEN")


def q(cur, s: str):
    cur.execute(s)
    return cur.fetchall() or []


def sh(x, w: int = 240) -> str:
    t = str(x) if x is not None else ""
    return t if len(t) <= w else t[: w - 1] + "…"


def add(
    rows: list,
    tid: object,
    criterio: str,
    sub: str,
    nivel: str,
    tabla: str,
    hecho: str,
) -> None:
    rows.append(
        {
            "ticket_id": tid,
            "criterio_iqs": criterio,
            "subcriterio": sub,
            "match": nivel,
            "tabla": tabla,
            "hecho_observable": hecho,
        }
    )


def main() -> int:
    rlist: list[dict] = []
    with sql.connect(
        server_hostname=host, http_path=http_path, access_token=token
    ) as conn:
        with conn.cursor() as cur:
            for row in q(
                cur,
                f"""
                SELECT ticket_id, field_name, count(*)
                FROM {FQN}.`s__general__zendesk_tickets_events__event`
                WHERE ticket_id IN ({T_IN})
                GROUP BY ticket_id, field_name
                """,
            ):
                add(
                    rlist,
                    row[0],
                    "Procesos en Zendesk (cambio de campo genérico)",
                    f"field_name = {row[1]}",
                    "directo",
                    "s__general__zendesk_tickets_events__event",
                    f"Número de eventos con ese campo: {row[2]}",
                )

            srows = q(
                cur,
                f"""
                SELECT ticket_id, event_timestamp, field_value
                FROM {FQN}.`s__general__zendesk_tickets_events__event`
                WHERE ticket_id IN ({T_IN}) AND lower(field_name) = 'status'
                ORDER BY ticket_id, event_timestamp
                """,
            )
            byt: dict[int, list] = defaultdict(list)
            for a in srows:
                byt[int(a[0])].append(f"{a[1]}: {a[2]}")
            for tid in TICKETS:
                seq = byt.get(tid, [])
                add(
                    rlist,
                    tid,
                    "Procesos Zendesk — Estado de la conversación",
                    "Línea de tiempo de status (referente a Jornada Guru)",
                    "directo" if seq else "no_datos",
                    "s__general__zendesk_tickets_events__event (field_name=status)",
                    " -> ".join(seq) if seq else "Sin filas con field_name=status",
                )

            for row in q(
                cur,
                f"""
                WITH x AS (
                  SELECT ticket_id, main_topic_normalized, secondary_topic_normalized, subtopic_normalized, subtopic_raw, created_at,
                    row_number() OVER (PARTITION BY ticket_id ORDER BY created_at DESC) AS rw
                  FROM {FQN}.`s__general__zendesk_ticket_topics__event`
                  WHERE ticket_id IN ({T_IN})
                )
                SELECT ticket_id, main_topic_normalized, secondary_topic_normalized, subtopic_normalized, subtopic_raw
                FROM x WHERE rw = 1
                """,
            ):
                add(
                    rlist,
                    row[0],
                    "Clasificación de la conversación — Tópico / subtópico",
                    "Última fila en lake (multitópico: revisar histórico si aplica)",
                    "directo",
                    "s__general__zendesk_ticket_topics__event",
                    sh(
                        f"main={row[1]!r}; sec={row[2]!r}; sub_n={row[3]!r}; sub_raw={row[4]!r}"
                    ),
                )

            for row in q(
                cur,
                f"""
                WITH n AS (
                  SELECT ticket_id, general_nature,
                    row_number() OVER (PARTITION BY ticket_id ORDER BY coalesce(sys_audit_updated_on, sys_audit_created_on) DESC) AS rw
                  FROM {FQN}.`s__general__zendesk_ticket_nature__event`
                  WHERE ticket_id IN ({T_IN})
                )
                SELECT ticket_id, general_nature FROM n WHERE rw = 1
                """,
            ):
                add(
                    rlist,
                    row[0],
                    "Clasificación de la conversación — Naturaleza",
                    "Última fila en lake",
                    "directo",
                    "s__general__zendesk_ticket_nature__event",
                    sh(f"general_nature={row[1]!r}"),
                )

            for row in q(
                cur,
                f"""
                WITH a AS (
                  SELECT ticket_id, group_id, assignee_id, guru_name, assignment_type, assignment_start_time,
                    row_number() OVER (PARTITION BY ticket_id ORDER BY coalesce(assignment_start_time) DESC) AS rw
                  FROM {FQN}.`s__general__zendesk_assignment__event`
                  WHERE ticket_id IN ({T_IN})
                )
                SELECT ticket_id, group_id, assignee_id, guru_name, assignment_type FROM a WHERE rw = 1
                """,
            ):
                add(
                    rlist,
                    row[0],
                    "Procesos Zendesk — cola / asignación (proxy; derivación depende de reglas tópico→equipo)",
                    "Último tramo de assignment",
                    "parcial",
                    "s__general__zendesk_assignment__event",
                    sh(
                        f"group_id={row[1]!r}, assignee_id={row[2]!r}, guru_name={row[3]!r}, type={row[4]!r}"
                    ),
                )

            for row in q(
                cur,
                f"""
                SELECT ticket_id, field_name, field_value, event_timestamp
                FROM {FQN}.`s__general__zendesk_tickets_events__event`
                WHERE ticket_id IN ({T_IN}) AND field_name IN ('group_id', 'assignee_id', 'sla')
                ORDER BY ticket_id, event_timestamp
                """,
            ):
                add(
                    rlist,
                    row[0],
                    "Procesos Zendesk — group / assignee / sla (tickets_events)",
                    row[1],
                    "directo",
                    "s__general__zendesk_tickets_events__event",
                    sh(f"{row[2]!r} @ {row[3]!r}"),
                )

            cf = q(
                cur,
                f"""
                SELECT ticket_id, field_name, field_value, updated_at
                FROM {FQN}.`s__tech__ticket_custom_fields__event`
                WHERE ticket_id IN ({T_IN})
                """,
            )
            if not cf:
                add(
                    rlist,
                    "todos 7189367..7243898",
                    "Procesos Zendesk — id org / partner (custom fields)",
                    "En lake s__tech__ticket_custom_fields__event",
                    "no_datos",
                    "s__tech__ticket_custom_fields__event",
                    "0 filas para los 5 tickets (no hay señal de custom en este stream para los ejemplos).",
                )
            else:
                for row in cf:
                    add(
                        rlist,
                        row[0],
                        "Procesos Zendesk — id org / partner (custom fields)",
                        f"field_name={row[1]!r}",
                        "directo",
                        "s__tech__ticket_custom_fields__event",
                        sh(f"value={row[2]!r}; updated={row[3]!r}"),
                    )

            ip = q(
                cur,
                f"""
                SELECT ticket_id, issue_problem_type, issue_problem_number, issue_problem_url, updated_at
                FROM {FQN}.`s__tech__ticket_issue_problem__event`
                WHERE ticket_id IN ({T_IN})
                """,
            )
            if not ip:
                add(
                    rlist,
                    "todos 7189367..7243898",
                    "Issues & Problems",
                    "Vinculación en lake",
                    "no_datos",
                    "s__tech__ticket_issue_problem__event",
                    "0 filas en ejemplos (no issue registrado o pipeline).",
                )
            else:
                for row in ip:
                    add(
                        rlist,
                        row[0],
                        "Issues & Problems",
                        f"type={row[1]!r}, n={row[2]!r}",
                        "directo",
                        "s__tech__ticket_issue_problem__event",
                        sh(
                            f"url={row[3]!r} updated={row[4]!r}"
                        ),
                    )

            for row in q(
                cur,
                f"""
                SELECT ticket_id, interaction_type, source, is_bot_interaction, count(*)
                FROM {FQN}.`s__general__zendesk_interactions__event`
                WHERE ticket_id IN ({T_IN})
                GROUP BY ticket_id, interaction_type, source, is_bot_interaction
                """,
            ):
                add(
                    rlist,
                    row[0],
                    "Contexto hilo (bots y canal) — criterio IQS separado; útil para auditar",
                    f"{row[1]!r} / {row[2]!r} / bot={row[3]!r}",
                    "parcial",
                    "s__general__zendesk_interactions__event",
                    f"recuento agregado: {row[4]}",
                )

            err_sc = None
            try:
                sc = q(
                    cur,
                    f"""
                    SELECT sd_ticket_id, sd_status, sd_group_id, sd_parent_ticket_id, sd_tags
                    FROM {FQN}.`g__general__side_conversations__agg_ticket`
                    WHERE sd_ticket_id IN ({T_IN})
                    """,
                )
            except Exception as e:  # noqa: BLE001
                sc = None
                err_sc = str(e)
            if err_sc:
                add(
                    rlist,
                    "—",
                    "Procesos Zendesk — Side conversation (agregado ticket)",
                    "g__general__side_conversations__agg_ticket",
                    "error",
                    "g__general__side_conversations__agg_ticket",
                    sh(err_sc),
                )
            elif not sc:
                for tid in TICKETS:
                    add(
                        rlist,
                        tid,
                        "Procesos Zendesk — Side conversation (agregado ticket, sd_ticket_id)",
                        "0 filas en g__ (sin side conv. en lake para el ticket, o aún no materializado)",
                        "no_datos",
                        "g__general__side_conversations__agg_ticket",
                        "Consulta con sd_ticket_id. Si hubo side conv. en UI y no en lake, revisar ETL.",
                    )
            else:
                for row in sc:
                    add(
                        rlist,
                        row[0],
                        "Procesos Zendesk — Side conversation (agregado ticket)",
                        "Vista g__ (sd_*); validar mapeo con Jornada Guru / Zendesk",
                        "parcial",
                        "g__general__side_conversations__agg_ticket",
                        sh(
                            f"sd_status={row[1]!r}, sd_group_id={row[2]!r}, parent={row[3]!r}, tags={row[4]!r}"
                        ),
                    )

            for tid in TICKETS:
                add(
                    rlist,
                    tid,
                    "Knowledge Base — consulta y feedback",
                    "Uso de artículos / registro en Slack",
                    "no_en_lake",
                    "—",
                    "No mapeable solo con eventos `data_cx` (p. ex. #support-documentación-feedback; requiere otras fuentes).",
                )

            mrows = q(
                cur,
                f"""
                SELECT ticket_id, field_name, field_value
                FROM {FQN}.`s__general__zendesk_tickets_events__event`
                WHERE ticket_id IN ({T_IN})
                  AND (
                    lower(cast(field_name as string)) LIKE '%merge%'
                    OR lower(cast(field_value as string)) LIKE '%merge%'
                    OR lower(cast(field_value as string)) LIKE '%duplicate%'
                    OR lower(cast(field_name as string)) LIKE '%duplic%'
                  )
                """,
            )
            for row in mrows or []:
                add(
                    rlist,
                    row[0],
                    "Procesos Zendesk — Duplicates (fusión) — búsqueda heurística en tickets_events",
                    f"Posible pista: {row[1]!r}",
                    "parcial",
                    "s__general__zendesk_tickets_events__event",
                    sh(f"field_value contiene pista: {row[2]!r}"),
                )
            if not mrows:
                add(
                    rlist,
                    f"{TICKETS[0]}..{TICKETS[-1]}",
                    "Procesos Zendesk — Duplicates (fusión)",
                    "Sin pista 'merge'/'duplic' en name/value de `tickets_events`",
                    "no_datos",
                    "s__general__zendesk_tickets_events__event (y otras tablas; validar con Pepe/ETL)",
                    "Fusión a veces solo en eventos con otros nombres; usar ticket de prueba con merge o doc de ETL.",
                )

    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ticket_id",
                "criterio_iqs",
                "subcriterio",
                "match",
                "tabla",
                "hecho_observable",
            ],
            delimiter=";",
        )
        w.writeheader()
        w.writerows(rlist)
    print("OK", OUT_CSV, "filas", len(rlist))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
