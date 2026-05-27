"""Catálogo de Tópicos / Subtópicos / Naturalezas — fuente: Google Sheet maestro.

Sheet: '[Global] [CX] Tópicos/Subtópicos List'
ID:    1OToNB4aEe5n5ciD--NngBCrUkWCdr6AoH_NyDZ2p63Y

El sheet vive fuera del lake y cambia con baja frecuencia (Doc&Comm lo
mantiene). Para no pegarle al Sheets API en cada evaluación, mantenemos un
cache CSV local (`_cache_topicos.csv`, `_cache_naturalezas.csv`) que se
regenera bajo demanda con `refrescar_cache()`.

Uso:

    from robia_procesos.core import topicos_catalogo as cat
    catalogo = cat.cargar_catalogo()        # devuelve Catalogo (carga cache)
    cat.refrescar_cache()                   # va al Sheet y reescribe cache
    catalogo.combinacion_valida("Online", "Configuraciones Online", "Idiomas Y Monedas")
    catalogo.naturaleza_valida("Duda/Dúvida Auto")

Decisiones de diseño:

- **Unión de geografías**: AR + LATAM + BR se combinan en un único catálogo.
  Esto evita falsos negativos cuando no podemos detectar la geo del ticket
  (ese cruce vendrá vía `s__tech__ticket_subdomains__event` en Fase 3).
- **Normalización tolerante**: claves se comparan en lowercase, sin tildes,
  sin caracteres separadores ('/', '-'). Esto absorbe diferencias ES/PT y
  variantes ortográficas del lake.
- **Aliases ES↔PT** para Naturaleza: el lake muestra valores híbridos como
  'Duda/Dúvida Auto'; los matcheamos a la naturaleza canónica.
"""
from __future__ import annotations

import csv
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SHEET_ID = "1OToNB4aEe5n5ciD--NngBCrUkWCdr6AoH_NyDZ2p63Y"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

_ROOT = Path(__file__).resolve().parents[2]
SA_PATH = _ROOT / "Credenciales" / "service_account_robia.json"
CACHE_TOPICOS = _ROOT / "_cache_topicos.csv"
CACHE_NATURALEZAS = _ROOT / "_cache_naturalezas.csv"

# Edad máxima del cache antes de avisar que conviene refrescar (no es error).
CACHE_TTL_DIAS = 14

# (nombre_hoja, fila_header_0idx, col_equipo_0idx | None, col_main_0idx, col_sec_0idx, col_sub_0idx)
# col_equipo None = hoja sin columna de equipo (LATAM genérico).
HOJAS_TOPICOS: list[tuple[str, int, int | None, int, int, int]] = [
    ("AR_Tópicos_Zendesk/Slack", 2, 0, 1, 2, 3),
    ("LATAM_Tópicos_Zendesk/Slack", 1, None, 0, 1, 2),
    ("[BR] Tópicos Zendesk/Slack", 1, 0, 1, 2, 5),
]

# (nombre_hoja, fila_inicio_datos, col_naturaleza_0idx)
HOJAS_NATURALEZA: list[tuple[str, int, int]] = [
    ("[LATAM] Naturaleza da Conversa", 9, 1),
    ("[BR] Natureza da Conversa", 9, 1),
]


# ---------- Normalización ----------

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalizar(s: str | None) -> str:
    """Lowercase, sin tildes, sin separadores. Vacío ↔ ''."""
    if s is None:
        return ""
    s = s.strip()
    if not s:
        return ""
    # NFKD descompone los caracteres acentuados (á → a + ´); descartamos los marks
    s = "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )
    s = s.lower()
    return _NON_WORD.sub("", s)


# Aliases ES↔PT que aparecen en el lake (`general_nature`) pero usan ortografía
# distinta a la del Sheet. Se matchean por forma normalizada.
NATURALEZA_ALIASES_NORM: dict[str, str] = {
    # lake → forma canónica (en español, sin tildes, sin separadores)
    normalizar("Duda/Dúvida Auto"): normalizar("Duda Autoatención"),
    normalizar("Duda/Dúvida Investigativa"): normalizar("Duda Investigativa"),
    normalizar("Dúvida Auto"): normalizar("Duda Autoatención"),
    normalizar("Dúvida Autoatendimento"): normalizar("Duda Autoatención"),
    normalizar("Dúvida Investigativa"): normalizar("Duda Investigativa"),
    normalizar("Problem/Feedback"): normalizar("Problem"),
}


# ---------- Estructura ----------


@dataclass(frozen=True)
class Catalogo:
    combinaciones_norm: frozenset[tuple[str, str, str]]
    subtopicos_norm: frozenset[str]
    naturalezas_norm: frozenset[str]
    # combinaciones agrupadas por hoja origen (clave = nombre de hoja del Sheet).
    # Permite validar contra una geo específica sin volver a leer el Sheet.
    combinaciones_por_hoja: dict[str, frozenset[tuple[str, str, str]]] = field(
        default_factory=dict
    )
    # solo para diagnóstico humano:
    combinaciones_originales: tuple[tuple[str, str, str], ...] = field(default=())
    # (equipo, main, sec, sub) — equipo viene de col 0 de hojas AR y BR
    # (LATAM no tiene col equipo, queda string vacío). Usado por sub-reglas
    # LLM para filtrar el catálogo por equipo del ticket.
    combinaciones_con_equipo: tuple[tuple[str, str, str, str], ...] = field(default=())
    naturalezas_originales: tuple[str, ...] = field(default=())

    def combinacion_valida(self, main: str, sec: str, sub: str) -> bool:
        return (normalizar(main), normalizar(sec), normalizar(sub)) in self.combinaciones_norm

    def combinacion_valida_en_hojas(
        self, main: str, sec: str, sub: str, hojas: Iterable[str]
    ) -> bool:
        """¿La tripla está en al menos una de las hojas indicadas?"""
        clave = (normalizar(main), normalizar(sec), normalizar(sub))
        for hoja in hojas:
            if clave in self.combinaciones_por_hoja.get(hoja, frozenset()):
                return True
        return False

    def subtopico_existe(self, sub: str) -> bool:
        return normalizar(sub) in self.subtopicos_norm

    def naturaleza_valida(self, valor: str) -> bool:
        if not valor:
            return False
        n = normalizar(valor)
        if n in self.naturalezas_norm:
            return True
        return NATURALEZA_ALIASES_NORM.get(n) in self.naturalezas_norm


# ---------- Lectura del Sheet (refresco) ----------


def _abrir_sheet():
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def _extraer_topicos(
    rows: list[list[str]],
    fila_header: int,
    c_equipo: int | None,
    c_main: int,
    c_sec: int,
    c_sub: int,
) -> list[tuple[str, str, str, str]]:
    """Devuelve tuplas (equipo, main, sec, sub).

    Aplica **fill-down** sobre la columna equipo: en el Sheet, los headers
    de equipo suelen estar en una sola fila y las filas siguientes heredan
    el valor (visualmente vía merge cells o porque humanos lo entienden).
    Acá los rellenamos explícitamente para que cada fila tenga su equipo.
    """
    out: list[tuple[str, str, str, str]] = []
    equipo_actual = ""
    cols_requeridas = [c_main, c_sec, c_sub]
    if c_equipo is not None:
        cols_requeridas.append(c_equipo)
    for fila in rows[fila_header + 1 :]:
        if max(cols_requeridas) >= len(fila):
            continue
        # Fill-down del equipo: si la celda tiene valor, actualizamos el "actual";
        # si está vacía, heredamos del anterior.
        if c_equipo is not None:
            celda_equipo = (fila[c_equipo] or "").strip()
            if celda_equipo:
                equipo_actual = celda_equipo
        sub = (fila[c_sub] or "").strip()
        if not sub:
            continue  # filas-separador: solo header de equipo o tópico, sin subtópico
        main = (fila[c_main] or "").strip()
        sec = (fila[c_sec] or "").strip()
        out.append((equipo_actual, main, sec, sub))
    return out


def _extraer_naturalezas(rows: list[list[str]], fila_inicio: int, c_nat: int) -> list[str]:
    out: list[str] = []
    for fila in rows[fila_inicio:]:
        if c_nat >= len(fila):
            continue
        v = (fila[c_nat] or "").replace("\n", " ").strip()
        if v:
            out.append(v)
    return out


def refrescar_cache() -> Catalogo:
    """Lee el Sheet y reescribe `_cache_topicos.csv` y `_cache_naturalezas.csv`."""
    sh = _abrir_sheet()
    combinaciones: list[tuple[str, str, str, str, str]] = []  # (geo, equipo, main, sec, sub)
    for nombre, fila_header, c_equipo, c_main, c_sec, c_sub in HOJAS_TOPICOS:
        rows = sh.worksheet(nombre).get_all_values()
        for equipo, main, sec, sub in _extraer_topicos(
            rows, fila_header, c_equipo, c_main, c_sec, c_sub
        ):
            combinaciones.append((nombre, equipo, main, sec, sub))

    naturalezas: list[tuple[str, str]] = []  # (geo, valor)
    for nombre, fila_inicio, c_nat in HOJAS_NATURALEZA:
        rows = sh.worksheet(nombre).get_all_values()
        for v in _extraer_naturalezas(rows, fila_inicio, c_nat):
            naturalezas.append((nombre, v))

    with CACHE_TOPICOS.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["geografia", "equipo", "topico_principal", "topico_secundario", "subtopico"]
        )
        w.writerows(combinaciones)

    with CACHE_NATURALEZAS.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["geografia", "naturaleza"])
        w.writerows(naturalezas)

    return _construir_desde_filas(combinaciones, naturalezas)


def _construir_desde_filas(
    combinaciones: list[tuple[str, str, str, str, str]],
    naturalezas: list[tuple[str, str]],
) -> Catalogo:
    """Construye Catalogo desde filas (geo, equipo, main, sec, sub)."""
    # Compat: las "triplas" sin equipo siguen siendo el contrato público
    # de `combinaciones_originales`.
    triplas = [(m, s, sb) for _, _, m, s, sb in combinaciones]
    cuartetos = [(eq, m, s, sb) for _, eq, m, s, sb in combinaciones]
    nats = [v for _, v in naturalezas]

    combinaciones_norm = frozenset(
        (normalizar(m), normalizar(s), normalizar(sb)) for m, s, sb in triplas
    )
    subtopicos_norm = frozenset(normalizar(sb) for _, _, sb in triplas)
    naturalezas_norm = frozenset(normalizar(v) for v in nats)

    por_hoja: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for hoja, _eq, m, s, sb in combinaciones:
        por_hoja[hoja].add((normalizar(m), normalizar(s), normalizar(sb)))
    combinaciones_por_hoja = {h: frozenset(s) for h, s in por_hoja.items()}

    return Catalogo(
        combinaciones_norm=combinaciones_norm,
        subtopicos_norm=subtopicos_norm,
        naturalezas_norm=naturalezas_norm,
        combinaciones_por_hoja=combinaciones_por_hoja,
        combinaciones_originales=tuple(triplas),
        combinaciones_con_equipo=tuple(cuartetos),
        naturalezas_originales=tuple(sorted(set(nats))),
    )


def _leer_cache() -> Catalogo:
    if not CACHE_TOPICOS.exists() or not CACHE_NATURALEZAS.exists():
        raise FileNotFoundError("cache no existe; correr refrescar_cache() primero")

    combinaciones: list[tuple[str, str, str, str, str]] = []
    with CACHE_TOPICOS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            # `equipo` puede faltar en caches viejos (pre-2026-05-13); default ""
            combinaciones.append(
                (
                    r["geografia"],
                    r.get("equipo", ""),
                    r["topico_principal"],
                    r["topico_secundario"],
                    r["subtopico"],
                )
            )

    naturalezas: list[tuple[str, str]] = []
    with CACHE_NATURALEZAS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            naturalezas.append((r["geografia"], r["naturaleza"]))

    return _construir_desde_filas(combinaciones, naturalezas)


def cargar_catalogo(refrescar_si_falta: bool = True) -> Catalogo:
    """Carga el catálogo desde cache; si falta, refresca desde el Sheet."""
    try:
        return _leer_cache()
    except FileNotFoundError:
        if not refrescar_si_falta:
            raise
        return refrescar_cache()


def edad_cache_dias() -> float | None:
    """Devuelve la edad del cache en días, o None si no existe."""
    if not CACHE_TOPICOS.exists():
        return None
    return (time.time() - CACHE_TOPICOS.stat().st_mtime) / 86400
