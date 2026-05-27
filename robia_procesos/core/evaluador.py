"""Orquestador — corre los 4 evaluadores de proceso sobre cada ticket.

Cada evaluador de proceso es una función ``(ticket_id) -> list[CriterioEvaluado]``
que emite todas las sub-reglas de su proceso (con resultados THUMBS_UP /
THUMBS_DOWN / NO_EVALUABLE).

Registro: ``EVALUADORES`` mapea nombre del proceso → función. A medida que
se implementan los evaluadores (Fase B), se van enchufando acá. Los que
todavía no existen emiten una lista vacía (4 filas N/A en el output).

Uso típico::

    from robia_procesos.core import evaluador
    criterios = evaluador.evaluar_ticket(7448501)
    filas = ensamblar_filas_ticket(7448501, "David", "AR", 20, criterios)
    escribir_filas(filas)
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from robia_procesos.core.contrato import CriterioEvaluado
from robia_procesos.core.output import (
    PROCESOS_ORDEN,
    FilaOutput,
    ensamblar_filas_ticket,
    semana_iso,
)

# Stubs vacíos — se sobrescriben en evaluador.registrar(...) o al importar
# los módulos de Fase B. Cada uno toma ticket_id y devuelve criterios.
EvaluadorProceso = Callable[[int], list[CriterioEvaluado]]


def _stub_vacio(ticket_id: int) -> list[CriterioEvaluado]:
    return []


EVALUADORES: dict[str, EvaluadorProceso] = {
    proceso: _stub_vacio for proceso in PROCESOS_ORDEN
}


def registrar(proceso: str, fn: EvaluadorProceso) -> None:
    """Registra un evaluador para un proceso. Sobrescribe el stub."""
    if proceso not in EVALUADORES:
        raise ValueError(
            f"Proceso desconocido: {proceso!r}. Esperados: {list(EVALUADORES)}"
        )
    EVALUADORES[proceso] = fn


def evaluar_ticket(ticket_id: int) -> list[CriterioEvaluado]:
    """Corre los 4 evaluadores sobre un ticket y concatena los criterios."""
    todos: list[CriterioEvaluado] = []
    for proceso, fn in EVALUADORES.items():
        try:
            criterios = fn(ticket_id)
        except Exception as e:
            # No queremos que un proceso roto rompa la corrida entera.
            # Emitimos NO_EVALUABLE como placeholder y seguimos.
            from robia_procesos.core.contrato import (
                Confianza,
                Resultado,
            )
            criterios = [
                CriterioEvaluado(
                    ticket_id=ticket_id,
                    criterio=proceso,
                    sub_regla="evaluador_error",
                    resultado=Resultado.NO_EVALUABLE,
                    regla=f"Error interno del evaluador: {type(e).__name__}: {e}",
                    confianza=Confianza.HEURISTICA,
                )
            ]
        todos.extend(criterios)
    return todos


def evaluar_batch(
    pares: list[tuple[int, str]],
    pais: str = "AR",
    fecha: datetime | None = None,
) -> list[FilaOutput]:
    """Evalúa lista de ``(ticket_id, guru)`` y devuelve filas listas para Sheet.

    Args:
        pares: lista de tuplas (ticket_id, nombre_guru).
        pais: AR/BR/LT. Por ahora todos los tickets de la corrida son del
            mismo país (en v2 se puede detectar por guru_name).
        fecha: usado para calcular semana ISO. Default = hoy.

    Returns:
        Lista de FilaOutput (4 por ticket).
    """
    if fecha is None:
        fecha = datetime.now()
    semana = semana_iso(fecha)

    todas_filas: list[FilaOutput] = []
    for ticket_id, guru in pares:
        criterios = evaluar_ticket(ticket_id)
        filas = ensamblar_filas_ticket(
            ticket_id=ticket_id,
            guru=guru,
            pais=pais,
            semana=semana,
            criterios=criterios,
        )
        todas_filas.extend(filas)
    return todas_filas
