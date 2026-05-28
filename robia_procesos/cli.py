"""CLI: ``python -m robia_procesos.cli {evaluar|auditoria|listar}``.

Subcomandos:
    evaluar    — corre reglas sueltas contra ticket_ids y escupe JSON.
    auditoria  — corre los 4 evaluadores de proceso sobre una lista
                 (CSV con ticket_id,guru) y escribe al Sheet de auditorías.
    listar     — lista las reglas registradas.

`auditoria` no consume tokens de Claude. Solo Zendesk API + Databricks +
OpenAI (para sub-reglas LLM cuando estén implementadas).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from robia_procesos.reglas import REGISTRO


# ────────────────────── Subcomando `evaluar` (reglas individuales) ──────────────────────


def cmd_evaluar(args: argparse.Namespace) -> int:
    ticket_ids = [int(t) for t in args.ticket_ids]
    nombres = args.reglas or list(REGISTRO.keys())
    desconocidas = [n for n in nombres if n not in REGISTRO]
    if desconocidas:
        print(
            f"Reglas desconocidas: {desconocidas}. Disponibles: {list(REGISTRO)}",
            file=sys.stderr,
        )
        return 2

    resultados = []
    for nombre in nombres:
        for ev in REGISTRO[nombre](ticket_ids):
            resultados.append(ev.to_dict())

    json.dump(resultados, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def cmd_listar(_: argparse.Namespace) -> int:
    for nombre in REGISTRO:
        print(nombre)
    return 0


# ────────────────────── Subcomando `auditoria` (batch → Sheet) ──────────────────────


def _cargar_pares_csv(path: Path) -> list[tuple[int, str]]:
    """Lee CSV con columnas ticket_id,guru (header obligatorio).

    Acepta delimitador `,` o tab. Tolera espacios alrededor.
    """
    text = path.read_text(encoding="utf-8-sig")
    # Detectar delimitador
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text[:1024], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    pares: list[tuple[int, str]] = []
    for row in reader:
        # nombres flexibles
        tid_raw = row.get("ticket_id") or row.get("Ticket_id") or row.get("Nº do Ticket") or row.get("ticket")
        guru = row.get("guru") or row.get("Guru") or ""
        if not tid_raw:
            continue
        try:
            pares.append((int(str(tid_raw).strip()), guru.strip()))
        except ValueError:
            print(f"WARN: ticket_id inválido: {tid_raw!r}, saltando", file=sys.stderr)
    return pares


def cmd_auditoria(args: argparse.Namespace) -> int:
    """Corre los evaluadores de proceso sobre una lista y escribe al Sheet."""
    from robia_procesos.core.output import (
        FilaOutput,
        _agregar_score,
        _formatear_rcs,
        _formatear_reasoning,
        semana_iso,
    )

    # Cargar lista
    csv_path = Path(args.csv).resolve()
    if not csv_path.exists():
        print(f"ERROR: no existe {csv_path}", file=sys.stderr)
        return 2
    pares = _cargar_pares_csv(csv_path)
    if not pares:
        print(f"ERROR: CSV vacío o sin columnas ticket_id/guru válidas", file=sys.stderr)
        return 2
    print(f"Cargados {len(pares)} tickets de {csv_path.name}", file=sys.stderr)

    # Determinar qué evaluadores correr
    procesos_solicitados = set(args.procesos) if args.procesos else None
    evaluadores = _evaluadores_disponibles()
    if procesos_solicitados:
        desconocidos = procesos_solicitados - set(evaluadores)
        if desconocidos:
            print(f"ERROR: procesos desconocidos: {desconocidos}", file=sys.stderr)
            print(f"  disponibles: {list(evaluadores)}", file=sys.stderr)
            return 2
        evaluadores = {k: v for k, v in evaluadores.items() if k in procesos_solicitados}

    print(f"Evaluadores activos: {list(evaluadores)}", file=sys.stderr)

    # Correr
    semana = semana_iso(datetime.now())
    filas: list[FilaOutput] = []
    for i, (ticket_id, guru) in enumerate(pares, 1):
        print(f"[{i:2d}/{len(pares)}] {ticket_id} — {guru}", file=sys.stderr, end=" ... ")
        for nombre_proceso, (criterio_label, evaluar_fn) in evaluadores.items():
            try:
                criterios = evaluar_fn(ticket_id)
            except Exception as e:
                print(f"\n  ERROR en {nombre_proceso}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            if not criterios:
                continue
            fila = FilaOutput(
                semana=semana,
                pais=args.pais,
                ticket_id=ticket_id,
                guru=guru,
                criterio=criterio_label,
                score=_agregar_score(criterios),
                reasoning=_formatear_reasoning(criterios),
                rcs=_formatear_rcs(criterios),
            )
            filas.append(fila)
        # cierre línea de progreso
        scores = [f.score for f in filas if f.ticket_id == ticket_id]
        print(f"scores={scores}", file=sys.stderr)

    # Escribir o dry-run
    if args.dry_run:
        print(f"\nDRY-RUN: {len(filas)} filas listas, NO escritas al Sheet.", file=sys.stderr)
        for f in filas:
            print(f"  {f.ticket_id} | {f.criterio} | score={f.score}")
        return 0

    from robia_procesos.core.sheets_writer import escribir_filas, worksheet_para_pais
    ws_name = worksheet_para_pais(args.pais)
    print(f"\nEscribiendo {len(filas)} filas al worksheet {ws_name!r}...", file=sys.stderr)
    resultado = escribir_filas(
        filas,
        reemplazar_existentes=not args.no_reemplazar,
        worksheet_name=ws_name,
    )
    print(f"  → {resultado}", file=sys.stderr)
    return 0


def _evaluadores_disponibles() -> dict:
    """Devuelve {nombre_proceso: (criterio_label_para_output, funcion_evaluadora)}.

    A medida que se implementan los 4 procesos, se enchufan acá. Hoy solo
    está Id Usuario/Org (Fase B1).
    """
    out: dict = {}
    # B1
    try:
        from robia_procesos.reglas.id_usuario_org import (
            CRITERIO as CRIT_ID,
            evaluar_id_usuario_org,
        )
        out["id_usuario_org"] = (CRIT_ID, evaluar_id_usuario_org)
    except ImportError:
        pass
    # B2 (parcial) — Estado da Conversa (3 de 4 sub-reglas, sin LLM)
    try:
        from robia_procesos.reglas.estado_conversa import (
            CRITERIO as CRIT_EC,
            evaluar_estado_conversa,
        )
        out["estado_conversa"] = (CRIT_EC, evaluar_estado_conversa)
    except ImportError:
        pass
    # B3 — Duplicates (pendiente)
    # B4 — Derivações (pendiente)
    return out


# ────────────────────── main ──────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="robia_procesos")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("evaluar", help="Evaluar reglas contra una lista de ticket_ids")
    pe.add_argument("ticket_ids", nargs="+", help="IDs de Zendesk a evaluar")
    pe.add_argument(
        "--reglas",
        nargs="+",
        help="Subconjunto de reglas a correr (default: todas las registradas)",
    )
    pe.set_defaults(func=cmd_evaluar)

    pa = sub.add_parser(
        "auditoria",
        help="Correr evaluadores de proceso sobre una lista (CSV) y escribir al Sheet",
    )
    pa.add_argument("csv", help="Path al CSV con columnas ticket_id,guru")
    pa.add_argument(
        "--pais",
        default="AR",
        help="País (AR/BR/LT). Default: AR. Va a la col B del Sheet.",
    )
    pa.add_argument(
        "--procesos",
        nargs="+",
        choices=["id_usuario_org", "estado_conversa", "duplicates", "derivacoes"],
        help="Subset de procesos a correr (default: todos los implementados)",
    )
    pa.add_argument(
        "--dry-run",
        action="store_true",
        help="Calcula filas pero NO escribe al Sheet — solo print",
    )
    pa.add_argument(
        "--no-reemplazar",
        action="store_true",
        help="No borrar filas previas del mismo ticket_id (append-only).",
    )
    pa.set_defaults(func=cmd_auditoria)

    pl = sub.add_parser("listar", help="Listar reglas registradas")
    pl.set_defaults(func=cmd_listar)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
