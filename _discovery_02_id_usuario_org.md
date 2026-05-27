# Discovery — Proceso 2: Id Usuario / Organización

**Fecha cierre:** 2026-05-27
**Calibrado contra:** tickets 6340206 (org no asociada), 6312826 (user WA no fusionado), 7097246 (partner sin Es Partner + Partner ID)
**Estado:** ✅ cerrado.

---

## Definición operativa

Verificar que el guru haya completado la información de identificación del merchant en el ticket: usuario fusionado (si aplica), organización asociada (siempre obligatoria), y datos de partner (si el merchant es partner).

---

## Regla 1 canónica de Sofía

> **TODO ticket DEBE tener organización asociada.** Si no la tiene, hay que asociarla.

Esto convierte la sub-regla 2.2 en la **más fácil de detectar** y la más estricta: no hay tickets "que legítimamente no tienen organización".

---

## Las 3 sub-reglas

### 2.1 No fusionó usuario (caso WhatsApp típico)

| | |
|---|---|
| **RC positiva (0)** | El user del ticket está fusionado con su user "principal" (email) o no hay duplicación de users. |
| **RC negativa (1)** | El ticket vino por WhatsApp/canal alternativo y existe otro user del mismo merchant con email registrado que no fue fusionado. |
| **Aplica si** | `via.channel = whatsapp` (u otro canal donde el user típicamente no tiene email). |
| **N/A si** | El canal de entrada ya es email (el user ya está identificado) o no hay otros users del mismo merchant. |
| **Señales** | Zendesk API: `get_ticket` (via.channel, requester_id, organization_id) + `/users/search?query=organization_id:X` para listar todos los users del merchant. |
| **LLM** | No. Reglas de heurística pura: si hay 2+ users con la misma org y el requester es el "no principal" (sin email) → señal. |
| **Gap** | Definir formalmente "user principal": ¿el más antiguo? ¿el que tiene email? ¿el que más tickets generó? — calibrar con Sofía. |

### 2.2 No asoció organización — REGLA CANÓNICA

| | |
|---|---|
| **RC positiva (0)** | `organization_id` está poblado al cierre del ticket. |
| **RC negativa (1)** | `organization_id IS NULL` al cierre del ticket. |
| **Aplica si** | SIEMPRE. Sofía: "TODO ticket DEBE tener organización asociada". |
| **N/A si** | Nunca. |
| **Señales** | Zendesk API: `get_ticket` → `organization_id`. |
| **LLM** | No. |
| **Gap** | Ninguno. Esta es **la sub-regla más simple y barata** del proyecto. |

**Bonus señal opcional:** custom field `360049131912` (categoría del seller) cuando vale `no_identificado` refuerza que el guru no identificó al merchant (no es bloqueante para la sub-regla).

### 2.3 No cargó Partner ID / No tildó "Es partner?"

Son **2 sub-reglas separadas** porque son 2 campos distintos del formulario.

#### 2.3a — Checkbox "Es partner?" no tildado

| | |
|---|---|
| **RC positiva (0)** | `9470656687892 = True` cuando el merchant es partner. |
| **RC negativa (1)** | `9470656687892` no poblado (o `False`) cuando el merchant es partner. |
| **Aplica si** | El merchant es identificado como partner (ver detección abajo). |
| **N/A si** | El merchant no es partner. |
| **Señales** | Zendesk API: `get_ticket.custom_fields[9470656687892]`. |

#### 2.3b — Partner ID no cargado

| | |
|---|---|
| **RC positiva (0)** | `33512019928340` (integer) poblado con el ID del partner. |
| **RC negativa (1)** | `33512019928340` vacío cuando el merchant es partner. |
| **Aplica si** | Mismo que 2.3a. |
| **N/A si** | Mismo que 2.3a. |
| **Señales** | Zendesk API: `get_ticket.custom_fields[33512019928340]`. |

#### Cómo detectar que "el merchant es partner" (precondición de 2.3)

Cualquiera de estos disparadores activa la sub-regla:
1. **Equipo del ticket = Success** (`9204146951188 = "success_equipe"`) — equipo dedicado a partners.
2. **Nota interna del ticket contiene URL** `https://stats.tiendanube.com/partner/profile?id=NNNN`. Detectable vía regex sobre `interactions__event` o vía Zendesk API `get_ticket_comments`.
3. **Custom field `4417024422804`** (Success tagger) tiene valor `es_partners`.

Si alguno de los 3 dispara → el ticket "es de partner" → 2.3a y 2.3b deben estar completos.

---

## Gap crítico del lake

`s__tech__ticket_custom_fields__event` solo trackea **2 field_names**: `support_feedback` y `type_of_task`. **Todos los demás custom fields (incluyendo equipo, partner, organización, etc.) NO están en el lake.** Confirmado con query: 0 filas para los 4 IDs de partner fields en los últimos 90 días.

**Implicancia:** las 3 sub-reglas de este proceso requieren **Zendesk API** (`get_ticket`). Por la cantidad de tickets a evaluar, conviene:
- Hacer una llamada API por ticket auditado.
- Cachear el resultado de `get_ticket` en `_cache_zendesk/tickets/{id}.json` para que las otras sub-reglas del proyecto que necesiten lo mismo no re-peguen.

---

## Constantes que el código va a necesitar

```python
# Custom fields del sistema (no son del ticket, son del requester/organization)
# Acceso via Zendesk API: ticket.requester.email, ticket.organization.id

# Custom fields del ticket relevantes para Id Usuario/Org
FIELD_TIENE_TIENDA = 4416670564116        # checkbox "Tiene tienda con nosotros?"
FIELD_ES_PARTNER = 9470656687892          # checkbox "Es partner?"
FIELD_PARTNER_ID = 33512019928340         # integer "[SOL] Partner ID"
FIELD_SUCCESS_TAG = 4417024422804         # tagger "Success" (Es Success / Top/Large / Partners)
FIELD_SELLER_TIPO = 360049131912          # categoría seller (vale 'no_identificado', 'top-seller', 'tiny-seller', etc.)

# Equipo Success (atiende partners)
EQUIPO_VALUE_SUCCESS = "success_equipe"   # valor del custom field 9204146951188

# Canales que típicamente requieren fusión manual de user
CANALES_SIN_EMAIL = {"whatsapp"}          # via.channel
```

---

## Tickets de referencia pedagógicos

- **6340206** — `organization_id=None`. Canal `native_messaging`. Equipo Pago Nube. Custom field `seller=no_identificado` confirma la falla.
- **6312826** — `via.channel=whatsapp`, `organization_id=361651419332`. El guru identificó la org pero no fusionó al user de WA con el user de email.
- **7097246** — Partner identificado en notas (`stats.tiendanube.com/partner/profile?id=2314`), pero `Es partner?` no tildado Y Partner ID vacío. Caso doble.

---

## Costo en tokens / API calls

- **Lake:** 0 queries necesarias para 2.2 (solo Zendesk API). 1 query Zendesk Search para 2.1 si activamos la detección de fusión faltante.
- **Zendesk API:** 1 llamada `get_ticket` por ticket auditado (compartida con otros procesos). 1 llamada extra `/users/search` solo para 2.1 cuando el canal es WhatsApp.
- **OpenAI:** 0. Las 3 sub-reglas son reglas determinísticas.
