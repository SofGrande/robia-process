"""Ensamblador de filas de output para la planilla de auditorías.

Cada ticket evaluado emite **4 filas** (1 por proceso). Cada fila tiene las
11 columnas A→K del formato acordado con Sofía (idéntico a Soft Skills).
Las columnas L→T quedan en blanco (las completa la auditora manualmente).

Reglas de agregación score por proceso:
    - TODAS las sub-reglas del proceso son NO_EVALUABLE → score = "N/A"
    - ALGUNA sub-regla es THUMBS_DOWN                   → score = "1"
    - El resto                                          → score = "0"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from robia_procesos.core.contrato import CriterioEvaluado, Resultado

# Mapping criterio (proceso) → orden de aparición en el output.
# El nombre debe coincidir EXACTO con el `criterio` que devuelve cada evaluador.
PROCESOS_ORDEN: tuple[str, ...] = (
    "Procesos Zendesk - Derivações",
    "Procesos Zendesk - Id Usuario/Org",
    "Procesos Zendesk - Duplicates",
    "Procesos Zendesk - Estado da Conversa",
)


@dataclass(frozen=True)
class FilaOutput:
    """Una fila del Sheet — 11 columnas A→K.

    Columnas L→T son completadas por la auditora manualmente; las dejamos
    como strings vacíos al escribir para no pisar fórmulas existentes.
    """
    semana: int                  # col A
    pais: str                    # col B (AR/BR/LT)
    ticket_id: int               # col C
    guru: str                    # col D
    criterio: str                # col E
    score: str                   # col F  ("0" / "1" / "N/A")
    score_calibrado: str = ""    # col G (manual)
    reasoning: str = ""          # col H
    rcs: str = ""                # col I
    calibracao_qa: str = ""      # col J (manual)
    notas: str = ""              # col K (manual)

    def to_row(self) -> list[str]:
        """Serializa a lista de 11 strings — orden A→K."""
        return [
            str(self.semana),
            self.pais,
            str(self.ticket_id),
            self.guru,
            self.criterio,
            self.score,
            self.score_calibrado,
            self.reasoning,
            self.rcs,
            self.calibracao_qa,
            self.notas,
        ]


def _agregar_score(criterios: list[CriterioEvaluado]) -> str:
    """Aplica las 3 reglas de agregación."""
    if not criterios:
        return "N/A"
    if all(c.resultado == Resultado.NO_EVALUABLE for c in criterios):
        return "N/A"
    if any(c.resultado == Resultado.THUMBS_DOWN for c in criterios):
        return "1"
    return "0"


def _formatear_reasoning(criterios: list[CriterioEvaluado]) -> str:
    """Enumera cada sub-regla y su resultado en formato leíble.

    Ejemplo de salida (3 sub-reglas de Derivaciones):

        Triagem derivó OK: macro 45429138530708 aplicada por Triagem
        a las 15:07:42 (author no es assignee). Equipo destino coherente.
        Guru derivó: N/A (no hubo derivación intermedia adicional).
        ADA: N/A (no pasó por ADA).
    """
    if not criterios:
        return ""
    bloques: list[str] = []
    for c in criterios:
        prefix = {
            Resultado.THUMBS_UP: "✓",
            Resultado.THUMBS_DOWN: "✗",
            Resultado.NO_EVALUABLE: "—",
        }[c.resultado]
        linea = f"{prefix} {c.sub_regla}: {c.regla}"
        if c.nota:
            linea += f" — {c.nota}"
        bloques.append(linea)
    return "\n".join(bloques)


def _formatear_rcs(criterios: list[CriterioEvaluado]) -> str:
    """Lista las RCs negativas aplicadas (texto del catálogo).

    Solo se llena cuando hay al menos un THUMBS_DOWN. Si hay varias,
    se concatenan con ' | '. Para sub-reglas sin error queda vacío.
    """
    rcs_negativas = [
        c.regla for c in criterios if c.resultado == Resultado.THUMBS_DOWN
    ]
    return " | ".join(rcs_negativas)


def ensamblar_filas_ticket(
    ticket_id: int,
    guru: str,
    pais: str,
    semana: int,
    criterios: list[CriterioEvaluado],
) -> list[FilaOutput]:
    """Toma todos los CriterioEvaluado de un ticket y devuelve 4 FilaOutput.

    Args:
        ticket_id: id del ticket evaluado.
        guru: último assignee humano (modelo mono-guru actual).
        pais: AR/BR/LT.
        semana: semana ISO del cierre.
        criterios: TODOS los CriterioEvaluado emitidos por los 4 evaluadores.

    Returns:
        Lista de 4 FilaOutput, una por proceso, en orden PROCESOS_ORDEN.
        Si un proceso no tiene criterios (raro), igual emite una fila
        con score N/A para mantener la simetría 4-filas-por-ticket.
    """
    # Agrupar criterios por proceso (cada CriterioEvaluado.criterio matchea
    # uno de PROCESOS_ORDEN).
    por_proceso: dict[str, list[CriterioEvaluado]] = {p: [] for p in PROCESOS_ORDEN}
    for c in criterios:
        if c.criterio in por_proceso:
            por_proceso[c.criterio].append(c)

    filas: list[FilaOutput] = []
    for proceso in PROCESOS_ORDEN:
        subs = por_proceso[proceso]
        filas.append(
            FilaOutput(
                semana=semana,
                pais=pais,
                ticket_id=ticket_id,
                guru=guru,
                criterio=proceso,
                score=_agregar_score(subs),
                reasoning=_formatear_reasoning(subs),
                rcs=_formatear_rcs(subs),
            )
        )
    return filas


def semana_iso(fecha: datetime) -> int:
    """Semana ISO del año (1-53). Se usa para llenar col A."""
    return fecha.isocalendar().week
