"""Cliente Zendesk REST API — wrapper mínimo para Macros y Help Center.

Credenciales en ``Credenciales/.env``: ``ZENDESK_SUBDOMAIN``, ``ZENDESK_EMAIL``,
``ZENDESK_TOKEN``. Acepta subdomain con o sin ``.zendesk.com`` / ``https://``.
Auth: HTTP Basic con ``"{email}/token:{token}"``.

Endpoints cubiertos:

- **Macros** (``/api/v2/macros``, ``/api/v2/macros/search``): catálogo completo
  con cache local. Soporta búsqueda por nombre (clave para detectar familias
  ``"Derivar para X"`` o ``"[AR] Acción:: Cerrar conversa duplicada"``).
- **Help Center** (``/api/v2/help_center/articles/search``,
  ``/api/v2/help_center/{locale}/articles/{id}``): búsqueda y lectura de
  artículos de la KB pública (Centro de Atención Nube).

Rate limit Zendesk Enterprise: ~700 req/min. Conservamos un sleep liviano
entre páginas para evitar tocar el techo en syncs largos.
"""
from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import requests
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / "Credenciales" / ".env")

CACHE_DIR = _ROOT / "_cache_zendesk"

# Locale por defecto. La cuenta de Tiendanube responde con ``es-419`` (LATAM
# general) cuando no se especifica. Para sub-reglas geo-específicas convendrá
# pasar el locale explícito (`es-ar`, `pt-br`).
LOCALE_DEFAULT = "es-419"

_PAGE_SLEEP_SECONDS = 0.2
_TIMEOUT = 30


def _credenciales() -> tuple[str, str, str]:
    subdomain = os.environ.get("ZENDESK_SUBDOMAIN")
    email = os.environ.get("ZENDESK_EMAIL")
    token = os.environ.get("ZENDESK_TOKEN")
    if not all([subdomain, email, token]):
        raise RuntimeError(
            "Faltan credenciales Zendesk en Credenciales/.env: "
            "ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_TOKEN."
        )
    return _clean_subdomain(subdomain), email, token  # type: ignore[arg-type]


def _clean_subdomain(raw: str) -> str:
    """Acepta ``tiendanubehelp``, ``tiendanubehelp.zendesk.com``,
    ``https://tiendanubehelp.zendesk.com/`` y devuelve solo ``tiendanubehelp``.
    """
    s = raw.strip()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    for suffix in (".zendesk.com/", ".zendesk.com"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip("/")


@lru_cache(maxsize=1)
def _base_url() -> str:
    subdomain, _, _ = _credenciales()
    return f"https://{subdomain}.zendesk.com"


@lru_cache(maxsize=1)
def _auth() -> tuple[str, str]:
    _, email, token = _credenciales()
    return (f"{email}/token", token)


def _request(method: str, path: str, params: dict | None = None) -> dict:
    """Wrapper de requests con auth y manejo de errores."""
    url = f"{_base_url()}{path}"
    resp = requests.request(
        method,
        url,
        auth=_auth(),
        params=params,
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Zendesk API {method} {path} -> {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _paginate(path: str, params: dict | None = None, key: str = "") -> Iterator[dict]:
    """Itera sobre una lista paginada de Zendesk (sigue ``next_page``).

    Args:
        path: ruta relativa, ej ``/api/v2/macros.json``.
        params: query params iniciales.
        key: nombre del array dentro del JSON. Si vacío, infiere por el
            nombre del path (ej. ``macros``, ``articles``).
    """
    if not key:
        key = path.rsplit("/", 1)[-1].split(".")[0]
    next_url: str | None = None
    while True:
        if next_url:
            resp = requests.get(next_url, auth=_auth(), timeout=_TIMEOUT)
            if not resp.ok:
                raise RuntimeError(
                    f"Zendesk paginate -> {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json()
        else:
            data = _request("GET", path, params=params)
        for item in data.get(key, []):
            yield item
        next_url = data.get("next_page")
        if not next_url:
            return
        time.sleep(_PAGE_SLEEP_SECONDS)


# ────────────────────────── Macros ──────────────────────────


def list_macros(active_only: bool = True) -> Iterator[dict]:
    """Itera todas las macros del workspace, paginando automáticamente.

    Args:
        active_only: si True, solo macros con ``active=True``. Filtrado local
            (Zendesk no expone filtro de actividad en este endpoint).
    """
    for macro in _paginate("/api/v2/macros.json", params={"per_page": 100}):
        if active_only and not macro.get("active", True):
            continue
        yield macro


def get_macro(macro_id: int) -> dict:
    """Devuelve la macro completa, incluyendo ``actions`` (el body del template)."""
    data = _request("GET", f"/api/v2/macros/{macro_id}.json")
    return data.get("macro", {})


def search_macros(query: str) -> Iterator[dict]:
    """Búsqueda full-text sobre nombres y contenido de macros."""
    yield from _paginate(
        "/api/v2/macros/search.json",
        params={"query": query, "per_page": 100},
        key="macros",
    )


def sync_macros_cache() -> int:
    """Descarga todas las macros activas a cache local y retorna el conteo.

    Cache en ``_cache_zendesk/macros.json``. Refrescar manualmente cuando
    Doc&Comm publique cambios en el catálogo.
    """
    import json

    CACHE_DIR.mkdir(exist_ok=True)
    macros = list(list_macros(active_only=True))
    cache_path = CACHE_DIR / "macros.json"
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(
            {"synced_at": time.time(), "count": len(macros), "macros": macros},
            f,
            ensure_ascii=False,
        )
    return len(macros)


# ────────────────────────── Help Center ──────────────────────────


def search_articles(
    query: str,
    locale: str = LOCALE_DEFAULT,
    per_page: int = 10,
) -> list[dict]:
    """Búsqueda full-text del Help Center, scoped al locale.

    El locale matter — sin él Zendesk devuelve el default de la cuenta, que
    puede mezclar idiomas. Locales relevantes: ``es-419`` (LATAM),
    ``es-ar`` (si existe específico), ``pt-br``.
    """
    data = _request(
        "GET",
        "/api/v2/help_center/articles/search.json",
        params={"query": query, "locale": locale, "per_page": per_page},
    )
    return data.get("results", [])


def get_article(article_id: int, locale: str | None = None) -> dict:
    """Devuelve un artículo del Help Center.

    Si ``locale`` se especifica, usa el endpoint scoped al locale. Si no,
    el endpoint genérico (Zendesk devuelve el default de la cuenta).
    """
    if locale:
        path = f"/api/v2/help_center/{locale}/articles/{article_id}.json"
    else:
        path = f"/api/v2/help_center/articles/{article_id}.json"
    data = _request("GET", path)
    return data.get("article", {})


# ────────────────────────── Tickets (subject + comments) ──────────────────────────


def get_ticket(ticket_id: int) -> dict:
    """Devuelve el ticket: subject, description, requester_id, group_id, custom_fields, etc.

    Lo que más usa el clasificador LLM: ``subject`` (línea de asunto), ``description``
    (cuerpo inicial), ``custom_fields`` (id 9204146951188 = equipo del ticket).
    """
    data = _request("GET", f"/api/v2/tickets/{ticket_id}.json")
    return data.get("ticket", {})


def get_ticket_comments(
    ticket_id: int,
    per_page: int = 10,
    sort_order: str = "desc",
) -> list[dict]:
    """Lista los comments del ticket — incluye ``body`` (HTML/plain) y ``author_id``.

    Por defecto últimos 10 en orden descendente (más reciente primero), igual
    que hace la app Clasificador de Tickets en Zendesk.
    """
    data = _request(
        "GET",
        f"/api/v2/tickets/{ticket_id}/comments.json",
        params={"per_page": per_page, "sort_order": sort_order},
    )
    return data.get("comments", [])


# Custom field IDs de Tiendanube Zendesk (confirmados via discovery 2026-05-13).
FIELD_ID_EQUIPO = 9204146951188   # equipo asignado al ticket (ej. "pago_nube_transacciones_y_cuenta")
FIELD_ID_BU = 9204049459220       # BU (ej. "pago_nube_bu", "smb_bu")


def get_ticket_custom_field(ticket_id: int, field_id: int) -> str | None:
    """Devuelve el valor de un custom field específico del ticket.

    Wrapper sobre :func:`get_ticket` que extrae solo el campo pedido. ``None``
    si el ticket no existe, el campo no aplica o el valor es vacío.
    """
    ticket = get_ticket(ticket_id)
    for cf in ticket.get("custom_fields", []) or []:
        if cf.get("id") == field_id:
            v = cf.get("value")
            if v in (None, "", False):
                return None
            return str(v)
    return None


@lru_cache(maxsize=256)
def get_group(group_id: int) -> dict:
    """Devuelve el grupo (equipo) por ID. Cacheado in-memory para no repetir."""
    data = _request("GET", f"/api/v2/groups/{group_id}.json")
    return data.get("group", {})


@lru_cache(maxsize=256)
def get_group_name(group_id: int) -> str:
    """Devuelve el nombre legible del grupo, o 'grupo N' si falla."""
    try:
        g = get_group(group_id)
        return g.get("name") or f"grupo {group_id}"
    except Exception:
        return f"grupo {group_id}"


def get_ticket_audits(ticket_id: int) -> list[dict]:
    """Devuelve la lista completa de audits del ticket (con paginación).

    Cada audit registra TODOS los cambios de campos del ticket con su autor
    (incluido el sistema con ``author_id = -1``). Es la única fuente de
    cambios de ``organization_id`` (el lake no los trackea).
    """
    return list(_paginate(f"/api/v2/tickets/{ticket_id}/audits.json", key="audits"))


def get_user(user_id: int) -> dict:
    """Devuelve el user completo: email, phone, organization_id, name, etc."""
    data = _request("GET", f"/api/v2/users/{user_id}.json")
    return data.get("user", {})


def search_users_by_org(organization_id: int) -> list[dict]:
    """Lista todos los usuarios de una organización via search."""
    data = _request(
        "GET",
        "/api/v2/users/search.json",
        params={"query": f"organization:{organization_id}", "per_page": 100},
    )
    return data.get("users", [])


def get_ticket_equipo(ticket_id: int) -> str | None:
    """Devuelve el equipo asignado al ticket en formato Zendesk
    (ej. ``"pago_nube_transacciones_y_cuenta"``).

    Para matchear con la columna Equipo del Sheet maestro usar
    :func:`robia_procesos.core.equipo_mapping.filtrar_por_equipo`,
    que tolera diferencias de formato entre fuentes.
    """
    return get_ticket_custom_field(ticket_id, FIELD_ID_EQUIPO)
