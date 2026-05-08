"""Contrato del evaluador: tipos compartidos entre reglas, runner y reporter.

Cada regla del paquete `robia_procesos.reglas` debe devolver una lista de
`CriterioEvaluado`. El runner agrega todas las listas y el reporter las
serializa a JSON / CSV. El contrato se mantiene chico a propósito: si una
regla necesita estructura adicional, va en su propio módulo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Resultado(str, Enum):
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    NO_EVALUABLE = "no_evaluable"


class Confianza(str, Enum):
    DIRECTA = "directa"          # SQL determinístico sobre eventos del lake
    PARCIAL = "parcial"          # cruce entre tablas / supuesto razonable
    HEURISTICA = "heuristica"    # juicio o LLM


@dataclass(frozen=True)
class Evidencia:
    """Hecho concreto del lake que soporta el resultado."""
    tabla: str
    descripcion: str
    timestamp: datetime | None = None
    valor: Any = None


@dataclass(frozen=True)
class CriterioEvaluado:
    ticket_id: int
    criterio: str               # nombre IQS, p.ej. "Procesos Zendesk - Estado de la conversación"
    sub_regla: str              # id corto, p.ej. "cierre_coherente"
    resultado: Resultado
    regla: str                  # descripción en lenguaje plano de qué se evaluó
    confianza: Confianza
    evidencia: tuple[Evidencia, ...] = field(default_factory=tuple)
    nota: str | None = None     # opcional: matices que el auditor humano debería ver

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "criterio": self.criterio,
            "sub_regla": self.sub_regla,
            "resultado": self.resultado.value,
            "regla": self.regla,
            "confianza": self.confianza.value,
            "evidencia": [
                {
                    "tabla": e.tabla,
                    "descripcion": e.descripcion,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "valor": e.valor,
                }
                for e in self.evidencia
            ],
            "nota": self.nota,
        }
