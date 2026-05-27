"""Reglas IQS — cada módulo evalúa un criterio del bloque 'Crítico para el Negocio'.

Cada regla expone una función `evaluar(ticket_ids: list[int]) -> list[CriterioEvaluado]`.
El runner las descubre y agrega los resultados.
"""
from __future__ import annotations

from collections.abc import Callable

from robia_procesos.core.contrato import CriterioEvaluado
from robia_procesos.reglas import (
    clasificacion_llm,
    clasificacion_naturaleza,
    clasificacion_topico,
    estado_conversacion,
    estado_pending_llm,
    feedback_stakeholders,
)

ReglaFn = Callable[[list[int]], list[CriterioEvaluado]]

REGISTRO: dict[str, ReglaFn] = {
    "estado_conversacion": estado_conversacion.evaluar,
    "estado_pending_llm": estado_pending_llm.evaluar,
    "clasificacion_topico": clasificacion_topico.evaluar,
    "clasificacion_naturaleza": clasificacion_naturaleza.evaluar,
    "clasificacion_llm": clasificacion_llm.evaluar,
    "feedback_stakeholders": feedback_stakeholders.evaluar,
}
