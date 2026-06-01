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
    "Derivações",
    "Id Usuario/Org",
    "Duplicates",
    "Estado da Conversa",
)


@dataclass(frozen=True)
class FilaOutput:
    """Una fila del Sheet — 11 cols A→K + 1 col extra `Aplicación` al final.

    Schema actualizado 2026-06-01: score binario 0/1 (sin N/A) + columna
    nueva `Aplicación` con valores "Aplicable" / "No aplicable".

    Posición de `Aplicación`: al **final** del row, después de las columnas
    manuales L-T del Sheet (que la auditora completa a mano). De esa manera
    no rompemos sus fórmulas existentes en L-T.
    """
    semana: int                  # col A
    pais: str                    # col B (AR/BR/LT)
    ticket_id: int               # col C
    guru: str                    # col D
    criterio: str                # col E
    score: str                   # col F  ("0" / "1" — binario)
    score_calibrado: str = ""    # col G (manual)
    reasoning: str = ""          # col H
    rcs: str = ""                # col I
    calibracao_qa: str = ""      # col J (manual)
    notas: str = ""              # col K (manual)
    aplicacion: str = ""         # col V  ("Aplicable" / "No aplicable")

    # Cantidad de columnas vacías entre col K (Notas) y col V (Aplicación).
    # Las cols L-U (Equipe, Puntaje, Cantidad de errores, Errores graves,
    # Errores medios, Tipo de Guru, Mês, Amostra, Data Fic) son manuales,
    # acá las dejamos vacías para no pisar lo que la auditora cargue.
    _GAP_COLUMNAS_MANUALES = 10  # cols L, M, N, O, P, Q, R, S, T, U

    def to_row(self) -> list[str]:
        """Serializa a lista de 22 strings — orden A→V."""
        return [
            str(self.semana),         # A
            self.pais,                # B
            str(self.ticket_id),      # C
            self.guru,                # D
            self.criterio,            # E
            self.score,               # F
            self.score_calibrado,     # G
            self.reasoning,           # H
            self.rcs,                 # I
            self.calibracao_qa,       # J
            self.notas,               # K
            *[""] * self._GAP_COLUMNAS_MANUALES,  # L-U (manuales, vacías)
            self.aplicacion,          # V
        ]


def _agregar_score(criterios: list[CriterioEvaluado]) -> str:
    """Score binario: 1 si hay error, 0 en cualquier otro caso.

    Reglas:
      - TODAS NO_EVALUABLE  → 0 (no aplicable)
      - ALGUNA THUMBS_DOWN  → 1 (error)
      - resto               → 0 (todo OK)
    """
    if not criterios:
        return "0"
    if all(c.resultado == Resultado.NO_EVALUABLE for c in criterios):
        return "0"
    if any(c.resultado == Resultado.THUMBS_DOWN for c in criterios):
        return "1"
    return "0"


def _agregar_aplicacion(criterios: list[CriterioEvaluado]) -> str:
    """Devuelve 'No aplicable' si TODAS las sub-reglas son NO_EVALUABLE, sino 'Aplicable'."""
    if not criterios:
        return "No aplicable"
    if all(c.resultado == Resultado.NO_EVALUABLE for c in criterios):
        return "No aplicable"
    return "Aplicable"


def _formatear_reasoning(criterios: list[CriterioEvaluado]) -> str:
    """Formato amigable para columna H — emoji por resultado + texto de la regla.

    Cada sub-regla debe redactar su ``regla`` como ``"{Etiqueta}: {descripción}"``
    en lenguaje conversacional, sin IDs largos ni nombres técnicos. Acá solo
    prependemos el emoji y concatenamos.

    Ejemplo de salida (4 sub-reglas de Id Usuario/Org en un caso OK):

        ✅ Organización: Asociada con éxito.
        ✅ Canal WhatsApp: El cliente ya tiene un correo registrado;
            no hizo falta fusionar cuentas.
        ⚪ Estado Partner: El ticket no pertenece a un partner.
        ⚪ Partner ID: El ticket no pertenece a un partner.
    """
    if not criterios:
        return ""
    EMOJI = {
        Resultado.THUMBS_UP: "✅",
        Resultado.THUMBS_DOWN: "❌",
        Resultado.NO_EVALUABLE: "⚪",
    }
    lineas: list[str] = []
    for c in criterios:
        linea = f"{EMOJI[c.resultado]} {c.regla}"
        if c.nota:
            linea += f" ({c.nota})"
        lineas.append(linea)
    return "\n".join(lineas)


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
                aplicacion=_agregar_aplicacion(subs),
            )
        )
    return filas


def semana_iso(fecha: datetime) -> int:
    """Semana ISO del año (1-53). Se usa para llenar col A."""
    return fecha.isocalendar().week
