"""IQS — Procesos Zendesk → Estado pendiente con respuesta completa (LLM).

Cuando un guru marca un ticket como 'pendiente', debe haber dejado una pregunta
de sondeo o action item para que el merchant avance respondiendo. Si la última
respuesta del guru antes del pendiente fue completa (toda la info entregada),
el ticket debería ser solved/snoozed, no pendiente.

Definición canónica (Sofía, Lección 6 de calibración del 7189367):

  "El estado pendiente se define cuando el guru necesita si o si una respuesta
  del merchant a su duda para poder avanzar con la resolución. Si no hay una
  pregunta clave de sondeo en su mensaje o un action item claro para que el
  merchant avance respondiendo, el estado pendiente está mal aplicado."

Pipeline:

1. Para cada ticket, identificar la última transición a ``pending`` en el lake.
2. Si nunca pasó por pending → ``NO_EVALUABLE`` (regla no aplica).
3. Traer el último comment del guru (Zendesk API) anterior a esa transición.
4. LLM clasifica si el comment incluye pregunta de sondeo / action item.
5. Emitir :class:`CriterioEvaluado`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape

from robia_procesos.core import db, llm, zendesk_api
from robia_procesos.core.contrato import (
    Confianza,
    CriterioEvaluado,
    Evidencia,
    Resultado,
)

CRITERIO = "Procesos Zendesk - Estado de la conversación"
SUB_REGLA = "pending_mantenido_con_respuesta_completa"
TABLA_EVENTS = "s__general__zendesk_tickets_events__event"

MAX_CHARS_RESPUESTA = 1500
MAX_TOKENS_RESPUESTA = 200

SYSTEM_PROMPT = """\
Eres un evaluador IQS de Tiendanube. Tu tarea: clasificar si la respuesta del guru \
a un merchant cumple con los criterios para que el ticket esté en estado 'Pendiente'.

DEFINICIÓN CANÓNICA (literal de la auditora humana):
"El estado pendiente se define cuando el guru necesita si o si una respuesta del \
merchant a su duda para poder avanzar con la resolución. Si no hay una pregunta clave \
de sondeo en su mensaje o un action item claro para que el merchant avance respondiendo, \
el estado pendiente está mal aplicado."

CRITERIOS:
- pendiente_aplica: la respuesta del guru contiene UNA PREGUNTA CLAVE DE SONDEO \
  (¿qué…?, ¿cómo…?, ¿podrías pasarme…?, ¿confirmás…?) O UN ACTION ITEM CLARO para el \
  merchant (envianos los datos X, necesitamos que confirmes Y, completá el formulario Z…).
- pendiente_no_aplica: la respuesta del guru es completa, informa al merchant sin \
  requerir respuesta para avanzar. Ejemplos: "Ya quedó solucionado", "Te confirmo \
  que se aplicó el reembolso", "Acá tienes el tutorial". Si el guru cierra con info \
  entregada sin acción pendiente del merchant, NO debería haber marcado pendiente.

NOTA OPERATIVA: ignorá saludos, agradecimientos y firmas. Lo que importa es si hay \
una pregunta o pedido que requiera una acción específica del merchant para avanzar.

Respondé ÚNICAMENTE con JSON válido (sin texto adicional):
{"clasificacion": "pendiente_aplica" | "pendiente_no_aplica", "razon": "<1-2 oraciones>"}
"""


def _to_datetime(v: object) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(
                v.replace("T", " ").replace("Z", "").split(".")[0]
            )
        except ValueError:
            return None
    return None


def _limpiar_html(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _proxima_transicion_hold_post_pending(
    ticket_id: int, ts_pending: datetime
) -> datetime | None:
    """Devuelve el primer ``hold`` posterior al pending, o ``None`` si no hubo.

    Usado para detectar el patrón "pending transitorio" cuando el guru marca
    pending como paso intermedio antes de abrir SD/issue y poner en hold.
    """
    rows = db.fetch(
        f"""
        SELECT event_timestamp
        FROM {db.FQN}.`{TABLA_EVENTS}`
        WHERE ticket_id = {int(ticket_id)}
          AND lower(field_name) = 'status'
          AND lower(field_value) = 'hold'
          AND event_timestamp > '{ts_pending.isoformat(sep=' ')}'
        ORDER BY event_timestamp ASC
        LIMIT 1
        """
    )
    if not rows:
        return None
    return _to_datetime(rows[0][0])


def _guru_assignee_en(ticket_id: int, ts: datetime) -> str | None:
    """¿Quién era el assignee del ticket en el momento ``ts``?

    Usa LEAD para encontrar el assignment cuyo ``assignment_start_time`` es
    el más reciente que cumple ``<= ts`` y cuya próxima asignación es ``> ts``.
    Esto identifica al guru responsable de la transición, no al último
    assignee del ticket (relevante cuando varios gurus tocan el ticket).
    """
    rows = db.fetch(
        f"""
        WITH a AS (
          SELECT
            guru_name,
            assignment_start_time,
            LEAD(assignment_start_time) OVER (
              ORDER BY assignment_start_time
            ) AS next_start
          FROM {db.FQN}.`s__general__zendesk_assignment__event`
          WHERE ticket_id = {int(ticket_id)} AND guru_name IS NOT NULL
        )
        SELECT guru_name
        FROM a
        WHERE assignment_start_time <= '{ts.isoformat(sep=' ')}'
          AND (next_start IS NULL OR next_start > '{ts.isoformat(sep=' ')}')
        ORDER BY assignment_start_time DESC
        LIMIT 1
        """
    )
    return rows[0][0] if rows else None


def _ultima_transicion_pending(ticket_id: int) -> datetime | None:
    rows = db.fetch(
        f"""
        SELECT event_timestamp
        FROM {db.FQN}.`{TABLA_EVENTS}`
        WHERE ticket_id = {int(ticket_id)}
          AND lower(field_name) = 'status'
          AND lower(field_value) = 'pending'
        ORDER BY event_timestamp DESC
        LIMIT 1
        """
    )
    if not rows:
        return None
    return _to_datetime(rows[0][0])


def _es_comment_de_guru(c: dict, requester_id: int | None) -> bool:
    """True si el comment es **respuesta pública** del guru o transcript del chat
    donde el guru intervino.

    Filtros (regla canónica Sofía 2026-05-13: "siempre evaluar la última respuesta
    pública del agente para determinar el estado correcto"):

    - ``public == True``: solo comments visibles al merchant.
    - ``via.channel != 'rule'``: excluye triggers automatizados (ej. "vamos a
      concluir la conversación...").
    - **Caso especial chat_transcript**: tickets atendidos por chat tienen su
      contenido en ``via.channel == 'chat_transcript'`` con ``author_id = -1``
      (Zendesk genera el transcript automáticamente). El body contiene la
      conversación completa con formato ``(HH:MM:SS) Nombre: mensaje``. Se
      considera respuesta del guru si el chat es público.
    - **Caso normal**: ``author_id != -1`` y ``!= requester_id``.
    """
    if not c.get("public"):
        return False
    via_channel = (c.get("via") or {}).get("channel", "")
    if via_channel == "rule":
        return False
    author_id = c.get("author_id")
    if via_channel == "chat_transcript":
        # Transcript público del chat: el guru intervino, el LLM evalúa.
        return True
    if author_id == -1 or author_id is None:
        return False
    if requester_id is not None and author_id == requester_id:
        return False
    return True


# Heurística para descartar comments que son "solo imagen" o "solo URL" —
# el LLM no puede juzgar sondeo sobre contenido no-textual. En esos casos
# seguimos buscando comments del guru anteriores.
_RE_MARKDOWN_IMAGEN = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_RE_URL = re.compile(r"https?://\S+")
_MIN_LETRAS_TEXTO = 20

# Tolerancia para chat_transcripts cuyo timestamp llega ligeramente DESPUÉS
# del pending: Zendesk genera el transcript cuando el chat termina, no en
# tiempo real, así que un pending marcado durante el chat puede tener su
# transcript hasta varios segundos/minutos después.
_VENTANA_CHAT_DESPUES_PENDING = 300  # 5 minutos en segundos

# Ventana posterior al pending para considerarlo "transitorio": si dentro de
# este tiempo el guru cambió el ticket a 'hold' (gestión interna), el pending
# fue un paso intermedio planificado, no uso incorrecto. La regla canónica
# (Sofía 2026-05-13): "En espera siempre debe haber una side conversation o
# un issue nuevo reportado" — esa validación la hace `hold_sin_side_conversation`,
# acá solo detectamos el patrón "pending → hold rápido".
#
# 60 minutos: caso real 7189367 mostró que un guru puede tardar ~40 min entre
# marcar pending y abrir SD/hold (mientras consulta o decide). Mismo-guru
# requerido para evitar contar como transitorio cuando otro guru posterior
# arregla el estado del primero.
_VENTANA_PENDING_TRANSITORIO = 3600  # 60 minutos en segundos


def _tiene_texto_significativo(body: str) -> bool:
    """¿El body contiene texto humano más allá de imágenes/URLs?

    Quita markdown de imagen y URLs sueltas, después mide letras (a-z con
    acentos). Si quedan menos de :data:`_MIN_LETRAS_TEXTO`, no es texto
    significativo para evaluar pregunta de sondeo.
    """
    if not body:
        return False
    sin_imagen = _RE_MARKDOWN_IMAGEN.sub("", body)
    sin_url = _RE_URL.sub("", sin_imagen)
    letras = re.findall(r"[a-záéíóúñü]", sin_url.lower())
    return len(letras) >= _MIN_LETRAS_TEXTO


def _ultimo_guru_comment_antes(
    ticket_id: int, ts_pending: datetime, requester_id: int | None
) -> dict | None:
    """Comment del guru "relacionado" con la transición a pending.

    Lógica de ventana temporal:

    - Comments con ``ts < ts_pending``: candidatos (regla normal).
    - Comments ``chat_transcript`` con ``ts >= ts_pending`` pero dentro de
      :data:`_VENTANA_CHAT_DESPUES_PENDING` segundos: también candidatos,
      porque Zendesk publica el transcript cuando el chat termina, lo cual
      puede ser segundos después del pending marcado durante el chat.

    Entre los candidatos elegimos el **más cercano** al ``ts_pending``
    (minimiza ``abs(ts - ts_pending)``). Eso captura la conversación real
    asociada a esa transición.

    Trae hasta 100 comments (Zendesk per_page max). Tickets más largos
    podrían perder el comment relevante — limitación v1.
    """
    try:
        comments = zendesk_api.get_ticket_comments(
            ticket_id, per_page=100, sort_order="desc"
        )
    except Exception:
        return None

    candidatos: list[dict] = []
    for c in comments:
        if not _es_comment_de_guru(c, requester_id):
            continue
        ts = _to_datetime(c.get("created_at"))
        if ts is None:
            continue
        delta = (ts - ts_pending).total_seconds()
        via_channel = (c.get("via") or {}).get("channel", "")
        # Aceptar si es anterior, o si es chat_transcript dentro de la ventana posterior.
        if delta > 0 and not (
            via_channel == "chat_transcript" and delta <= _VENTANA_CHAT_DESPUES_PENDING
        ):
            continue
        body = c.get("plain_body") or _limpiar_html(c.get("body"))
        if not _tiene_texto_significativo(body):
            continue
        candidatos.append(
            {
                "timestamp": ts,
                "body": body,
                "author_id": c.get("author_id"),
                "via_channel": via_channel,
                "delta_seconds": delta,
            }
        )

    if not candidatos:
        return None
    # El más cercano al pending (anterior o chat_transcript inmediatamente posterior).
    return min(candidatos, key=lambda x: abs(x["delta_seconds"]))


def _clasificar(body: str) -> dict:
    raw = llm.chat(
        system=SYSTEM_PROMPT,
        user=body[:MAX_CHARS_RESPUESTA],
        response_format={"type": "json_object"},
        max_tokens=MAX_TOKENS_RESPUESTA,
    )
    return json.loads(raw)


_REGLA_DESCR = (
    "El estado 'pendiente' se aplica cuando el guru necesita respuesta del merchant "
    "para avanzar. Si la última respuesta del guru antes de poner el ticket en pendiente "
    "no contiene pregunta de sondeo ni action item claro, el pendiente fue mal aplicado "
    "(debería haber sido solved/snoozed)."
)


def _evidencia_guru_responsable(guru_name: str | None) -> Evidencia:
    return Evidencia(
        tabla="zendesk_assignment__event",
        descripcion="Guru responsable de la transición a pending",
        valor=guru_name or "(sin assignee)",
    )


def evaluar(ticket_ids: list[int]) -> list[CriterioEvaluado]:
    resultados: list[CriterioEvaluado] = []
    for tid in ticket_ids:
        ts_pending = _ultima_transicion_pending(tid)
        if ts_pending is None:
            resultados.append(
                CriterioEvaluado(
                    ticket_id=tid,
                    criterio=CRITERIO,
                    sub_regla=SUB_REGLA,
                    resultado=Resultado.NO_EVALUABLE,
                    regla=_REGLA_DESCR,
                    confianza=Confianza.HEURISTICA,
                    nota="El ticket nunca pasó por estado 'pending'.",
                )
            )
            continue

        # Atribuir la transición al guru asignado EN ESE MOMENTO, no al último.
        guru_responsable = _guru_assignee_en(tid, ts_pending)

        # Fix de pending transitorio: si EL MISMO GURU pasó a 'hold' poco después,
        # el pending fue gestión interna planificada (ej. abrir SD/issue),
        # no error de aplicación. La validación de "hold válido" la hace
        # `hold_sin_side_conversation`.
        ts_hold_after = _proxima_transicion_hold_post_pending(tid, ts_pending)
        if ts_hold_after is not None:
            delta_hold = (ts_hold_after - ts_pending).total_seconds()
            if delta_hold <= _VENTANA_PENDING_TRANSITORIO:
                guru_hold = _guru_assignee_en(tid, ts_hold_after)
                mismo_guru = (
                    guru_responsable is not None
                    and guru_hold is not None
                    and guru_responsable == guru_hold
                )
                if mismo_guru:
                    resultados.append(
                        CriterioEvaluado(
                            ticket_id=tid,
                            criterio=CRITERIO,
                            sub_regla=SUB_REGLA,
                            resultado=Resultado.THUMBS_UP,
                            regla=_REGLA_DESCR,
                            confianza=Confianza.HEURISTICA,
                            evidencia=(
                                _evidencia_guru_responsable(guru_responsable),
                                Evidencia(
                                    tabla=TABLA_EVENTS,
                                    descripcion="Transición a 'pending'",
                                    timestamp=ts_pending,
                                ),
                                Evidencia(
                                    tabla=TABLA_EVENTS,
                                    descripcion=(
                                        f"Transición a 'hold' del mismo guru "
                                        f"({delta_hold/60:.1f} min después)"
                                    ),
                                    timestamp=ts_hold_after,
                                ),
                            ),
                            nota=(
                                f"Pending transitorio: {guru_responsable} pasó a 'hold' "
                                f"{delta_hold/60:.1f} min después → gestión interna "
                                "planificada. La validez del hold la evalúa "
                                "`hold_sin_side_conversation`."
                            ),
                        )
                    )
                    continue

        try:
            ticket = zendesk_api.get_ticket(tid)
        except Exception as e:
            resultados.append(
                CriterioEvaluado(
                    ticket_id=tid,
                    criterio=CRITERIO,
                    sub_regla=SUB_REGLA,
                    resultado=Resultado.NO_EVALUABLE,
                    regla=_REGLA_DESCR,
                    confianza=Confianza.HEURISTICA,
                    evidencia=(_evidencia_guru_responsable(guru_responsable),),
                    nota=f"Falló get_ticket: {type(e).__name__}: {e}",
                )
            )
            continue

        requester_id = ticket.get("requester_id")
        guru_comment = _ultimo_guru_comment_antes(tid, ts_pending, requester_id)

        if guru_comment is None:
            resultados.append(
                CriterioEvaluado(
                    ticket_id=tid,
                    criterio=CRITERIO,
                    sub_regla=SUB_REGLA,
                    resultado=Resultado.NO_EVALUABLE,
                    regla=_REGLA_DESCR,
                    confianza=Confianza.HEURISTICA,
                    evidencia=(
                        _evidencia_guru_responsable(guru_responsable),
                        Evidencia(
                            tabla=TABLA_EVENTS,
                            descripcion="Última transición a 'pending'",
                            timestamp=ts_pending,
                        ),
                    ),
                    nota=(
                        f"No se encontró comment del guru anterior a la transición a pending "
                        f"({ts_pending})."
                    ),
                )
            )
            continue

        try:
            inferido = _clasificar(guru_comment["body"])
        except Exception as e:
            resultados.append(
                CriterioEvaluado(
                    ticket_id=tid,
                    criterio=CRITERIO,
                    sub_regla=SUB_REGLA,
                    resultado=Resultado.NO_EVALUABLE,
                    regla=_REGLA_DESCR,
                    confianza=Confianza.HEURISTICA,
                    evidencia=(_evidencia_guru_responsable(guru_responsable),),
                    nota=f"Falló clasificación LLM: {type(e).__name__}: {e}",
                )
            )
            continue

        clasificacion = (inferido.get("clasificacion") or "").strip().lower()
        razon_llm = (inferido.get("razon") or "").strip()

        if clasificacion == "pendiente_aplica":
            resultado = Resultado.THUMBS_UP
        elif clasificacion == "pendiente_no_aplica":
            resultado = Resultado.THUMBS_DOWN
        else:
            resultado = Resultado.NO_EVALUABLE

        body_preview = guru_comment["body"][:300].replace("\n", " ")
        evidencia = (
            _evidencia_guru_responsable(guru_responsable),
            Evidencia(
                tabla=TABLA_EVENTS,
                descripcion="Última transición a 'pending'",
                timestamp=ts_pending,
            ),
            Evidencia(
                tabla="Zendesk API ticket comments",
                descripcion="Último comment del guru (preview)",
                timestamp=guru_comment["timestamp"],
                valor=body_preview + ("..." if len(guru_comment["body"]) > 300 else ""),
            ),
            Evidencia(
                tabla="LLM (estado_pending_llm)",
                descripcion="Clasificación + razón",
                valor=f"{clasificacion}: {razon_llm}",
            ),
        )

        resultados.append(
            CriterioEvaluado(
                ticket_id=tid,
                criterio=CRITERIO,
                sub_regla=SUB_REGLA,
                resultado=resultado,
                regla=_REGLA_DESCR,
                confianza=Confianza.HEURISTICA,
                evidencia=evidencia,
                nota=razon_llm or None,
            )
        )

    return resultados
