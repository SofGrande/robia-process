"""CLI mínima: ``python -m robia_procesos.cli evaluar <ticket_id> [<ticket_id> ...]``.

Salida: JSON por stdout (una lista de CriterioEvaluado serializados).
La idea es que esto sea componible: pipeable a `jq`, redirigible a archivo, o
embebible en un notebook.
"""
from __future__ import annotations

import argparse
import json
import sys

from robia_procesos.reglas import REGISTRO


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

    pl = sub.add_parser("listar", help="Listar reglas registradas")
    pl.set_defaults(func=cmd_listar)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
