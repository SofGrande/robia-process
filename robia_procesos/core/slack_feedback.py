"""Cliente Slack — lectura batch de los rooms de feedback CX.

Los 7 rooms públicos (requieren bot invitado a cada uno):

- ``bot-feedback-ar``, ``bot-feedback-br``
- ``macros-feedback-ar-mx``, ``macros-feedback-br``
- ``cx-documentacion-feedback-ar-mx``
- ``can-feedback-ar``, ``can-feedback-mx-co``

Estrategia: el evaluador no consulta Slack en cada ticket. Hay un sync masivo
que descarga el histórico de cada room a ``_cache_slack/<canal>.json`` y las
búsquedas se hacen sobre cache local. Refresh incremental con param ``oldest``
(timestamp del último sync por canal).

Uso típico::

    from robia_procesos.core import slack_feedback as sf
    sf.sync_canales()                       # primera vez, ~1-2 min
    sf.sync_canales(incremental=True)       # refresh diario
    sf.find_feedback(7189367)               # lista de mensajes con el ticket_id

Credencial: ``SLACK_BOT_TOKEN`` (xoxb-...) en ``Credenciales/.env``. Scopes
mínimos del bot: ``channels:history`` (lectura) + ``channels:read`` (resolver
nombres). ``users:read`` opcional para resolver autor del feedback.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests
from dotenv import load_dotenv

# Mapping nombre → channel_id confirmado por Sofía (2026-05-12).
# El nombre se usa como filename del cache; el id se manda a la API.
CANALES: dict[str, str] = {
    "bot-feedback-ar": "C04GJPZGN5U",
    "bot-feedback-br": "C04GAMQ3QMD",
    "macros-feedback-ar-mx": "C015TLJPCQ5",
    "macros-feedback-br": "C016JM8AZDJ",
    "cx-documentacion-feedback-ar-mx": "C030VVD7GPP",
    "can-feedback-ar": "C95FAFHUL",
    "can-feedback-mx-co": "C01TRHA84FR",
}

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / "Credenciales" / ".env")

CACHE_DIR = _ROOT / "_cache_slack"
SLACK_API = "https://slack.com/api"

# Slack Tier 3 (conversations.history) permite ~50 req/min. 1.2s entre páginas
# nos deja con margen sin lanzar 429.
_PAGE_SLEEP_SECONDS = 1.2
_PAGE_SIZE = 200
_TIMEOUT = 30


@dataclass(frozen=True)
class MensajeSlack:
    """Mensaje que mencionó un ticket en alguno de los rooms de feedback."""

    channel_id: str
    channel_name: str
    ts: str
    user_id: str | None
    text: str

    def iso_timestamp(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(float(self.ts)))

    def permalink(self) -> str:
        # Slack acepta el ts sin punto en URLs de permalink legacy.
        ts_compact = self.ts.replace(".", "")
        return f"https://slack.com/archives/{self.channel_id}/p{ts_compact}"


def _token() -> str:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Falta SLACK_BOT_TOKEN en Credenciales/.env. Agregar línea: "
            "SLACK_BOT_TOKEN=xoxb-... (el token del bot de la app de Slack)."
        )
    return token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def _cache_path(channel_name: str) -> Path:
    return CACHE_DIR / f"{channel_name}.json"


def _fetch_history(channel_id: str, oldest: float | None = None) -> Iterator[dict]:
    """Yield mensajes crudos de ``conversations.history`` con paginación."""
    cursor: str | None = None
    while True:
        params: dict[str, object] = {"channel": channel_id, "limit": _PAGE_SIZE}
        if oldest is not None:
            params["oldest"] = f"{oldest:.6f}"
        if cursor is not None:
            params["cursor"] = cursor

        resp = requests.get(
            f"{SLACK_API}/conversations.history",
            headers=_headers(),
            params=params,
            timeout=_TIMEOUT,
        )
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            hint = ""
            if error == "not_in_channel":
                hint = (
                    " — El bot no está invitado al canal. En Slack escribir "
                    "`/invite @<nombre-del-bot>` en el canal y reintentar."
                )
            elif error == "missing_scope":
                hint = (
                    " — Falta el scope `channels:history` en la app. "
                    "Agregarlo en api.slack.com/apps y reinstalar la app."
                )
            elif error == "invalid_auth":
                hint = " — Token inválido o expirado. Regenerar en api.slack.com."
            raise RuntimeError(f"Slack API error en {channel_id}: {error}{hint}")

        for msg in data.get("messages", []):
            yield msg

        if not data.get("has_more"):
            return
        cursor = data.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            return
        time.sleep(_PAGE_SLEEP_SECONDS)


def sync_canales(
    canales: list[str] | None = None,
    incremental: bool = False,
) -> dict[str, int]:
    """Descarga el histórico de los canales y lo persiste en cache local.

    Args:
        canales: subset por nombre. ``None`` = todos.
        incremental: si ``True`` y hay cache, solo trae mensajes posteriores
            al último ``ts`` ya almacenado. Si ``False``, baja todo de cero.

    Returns:
        ``{channel_name: total_mensajes_en_cache}`` (incluye merge con previos).
    """
    CACHE_DIR.mkdir(exist_ok=True)
    targets = canales or list(CANALES)
    desconocidos = [c for c in targets if c not in CANALES]
    if desconocidos:
        raise ValueError(
            f"Canal(es) desconocido(s): {desconocidos}. Válidos: {list(CANALES)}"
        )

    resultado: dict[str, int] = {}
    for name in targets:
        channel_id = CANALES[name]
        cache_path = _cache_path(name)

        existing: list[dict] = []
        oldest: float | None = None
        if incremental and cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                payload = json.load(f)
            existing = payload.get("messages", [])
            if existing:
                oldest = max(float(m["ts"]) for m in existing)

        nuevos = list(_fetch_history(channel_id, oldest=oldest))

        # Dedup por ts (único dentro del canal) — merge con existentes.
        por_ts = {m["ts"]: m for m in existing}
        for m in nuevos:
            por_ts[m["ts"]] = m
        merged = sorted(por_ts.values(), key=lambda m: float(m["ts"]))

        payload = {
            "channel_id": channel_id,
            "channel_name": name,
            "last_sync_ts": time.time(),
            "messages": merged,
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        resultado[name] = len(merged)

    return resultado


def find_feedback(
    ticket_id: int,
    canales: list[str] | None = None,
) -> list[MensajeSlack]:
    """Busca mensajes que mencionan ``ticket_id`` en el cache local.

    Match por substring exacto del id en el texto del mensaje. Los gurus
    suelen pegar el ID directo o como link de Zendesk (`/tickets/<id>`), ambos
    casos contienen el id como substring.

    Si el cache no existe para un canal, se omite silenciosamente (correr
    ``sync_canales()`` antes).
    """
    targets = canales or list(CANALES)
    needle = str(ticket_id)
    encontrados: list[MensajeSlack] = []
    for name in targets:
        cache_path = _cache_path(name)
        if not cache_path.exists():
            continue
        with open(cache_path, encoding="utf-8") as f:
            payload = json.load(f)
        for msg in payload.get("messages", []):
            text = msg.get("text") or ""
            if needle in text:
                encontrados.append(
                    MensajeSlack(
                        channel_id=payload["channel_id"],
                        channel_name=payload["channel_name"],
                        ts=msg["ts"],
                        user_id=msg.get("user"),
                        text=text,
                    )
                )
    return encontrados


def auth_test() -> dict:
    """Pega a ``auth.test`` para verificar token + scopes (smoke previo al sync)."""
    resp = requests.get(
        f"{SLACK_API}/auth.test", headers=_headers(), timeout=_TIMEOUT
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(
            f"auth.test falló: {data.get('error')}. "
            "Verificar SLACK_BOT_TOKEN en Credenciales/.env."
        )
    return data
