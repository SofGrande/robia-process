"""Reglas IQS — cada módulo evalúa un criterio del bloque 'Crítico para el Negocio'.

Cada regla expone una función `evaluar(ticket_ids: list[int]) -> list[CriterioEvaluado]`.
El runner las descubre y agrega los resultados.
"""
from __future__ import annotations

from collections.abc import Callable

from robia_procesos.core.contrato import CriterioEvaluado
from robia_procesos.reglas import (
    clasificacion_naturaleza,
    clasificacion_topico,
    estado_conversacion,
)

ReglaFn = Callable[[list[int]], list[CriterioEvaluado]]

REGISTRO: dict[str, ReglaFn] = {
    "estado_conversacion": estado_conversacion.evaluar,
    "clasificacion_topico": clasificacion_topico.evaluar,
    "clasificacion_naturaleza": clasificacion_naturaleza.evaluar,
}
