# Discovery — Proceso 4: Estado da Conversa

**Fecha cierre:** 2026-05-27
**Calibrado contra:** ticket 7239962 (cubre 4.1 y 4.3 en el mismo ticket)
**Estado:** ✅ cerrado.

---

## Definición operativa

Verificar que el guru usó correctamente cada uno de los 4 estados de Zendesk según las reglas operativas IQS:
- **Pending** — solo cuando espera respuesta del merchant a una pregunta de sondeo.
- **Hold** — pausa el SLA porque se espera input externo (otro equipo via SD, o issue/problem reportado).
- **Resolved / Snoozed** — cierre del ticket.

**Naturaleza transversal:** según la decisión 2026-05-12, Estado da Conversa se evalúa en **cada mensaje** del ticket, no como un proceso aparte al final.

---

## Las 4 sub-reglas

### 4.1 Pending mantenido con respuesta completa

| | |
|---|---|
| **RC positiva (0)** | Pending aplicado solo cuando hay pregunta de sondeo / action item para el merchant. |
| **RC negativa (1)** | Pending aplicado después de una respuesta completa (no quedan preguntas abiertas). |
| **Aplica si** | El ticket pasó por estado `pending` al menos una vez. |
| **N/A si** | Nunca pasó por pending. |
| **Señales** | 1) `tickets_events` con `field_name=status`, `field_value=pending` y timestamps. 2) `get_ticket_comments` del guru anterior a esa transición. 3) LLM clasifica si el último comment incluye pregunta/action item. |
| **LLM** | Sí — la definición canónica de Sofía es semántica. |
| **Estado código** | Ya implementado en [robia_procesos/reglas/estado_pending_llm.py](robia_procesos/reglas/estado_pending_llm.py), sin tests. Falta calibración contra tickets reales. |
| **Gap** | Calibrar prompt contra el caso 7239962 (Rosario, 6 abril 14:14, después de explicar que es Problem reportado → debió ser solved/snooze). |

**Definición canónica Sofía (usar literal en system prompt):**
> *"El estado pendiente se define cuando el guru necesita si o si una respuesta del merchant a su duda para poder avanzar con la resolución. Si no hay una pregunta clave de sondeo en su mensaje o un action item claro para que el merchant avance respondiendo, el estado pendiente está mal aplicado."*

### 4.2 Hold sin Side Conversation abierta

| | |
|---|---|
| **RC positiva (0)** | Tickets en hold tienen una SD activa en el período. |
| **RC negativa (1)** | Hold > 24h sin SD activa. |
| **Aplica si** | El ticket tuvo tramos de hold. |
| **N/A si** | Nunca pasó por hold. |
| **Señales** | `tickets_events` para tramos de hold + `g__general__side_conversations__agg_ticket` por `sd_parent_ticket_id`. |
| **LLM** | No. |
| **Estado código** | Ya implementado: sub-regla `hold_sin_side_conversation` (commit `48c4d01`, bug del `sd_ticket_id` → `sd_parent_ticket_id` ya arreglado). |
| **Gap** | Ninguno. |

### 4.3 Hold por Issue/Problem sin macro de Issue no triaged

| | |
|---|---|
| **RC positiva (0)** | Tickets con I/P relacionado y status hold tienen aplicada macro `1900012469807` (AR) o `5249935349524` (BR). |
| **RC negativa (1)** | Ticket pasó a hold por causa de un I/P pero la macro `Issue no triaged` NO se aplicó. |
| **Aplica si** | Ticket tiene I/P asociado (ver detección abajo) Y pasó a hold. |
| **N/A si** | No hay I/P asociado o no pasó a hold. |
| **Señales** | Ver "Detección de ticket con I/P" abajo. |
| **LLM** | No. Determinístico. |
| **Gap** | Ninguno (validado con 7239962). |

#### Detección de "ticket tiene I/P asociado"

Cualquiera de estos disparadores indica que el ticket está vinculado a un I/P:

1. **Custom fields poblados** (Zendesk API, NO están en lake):
   - `38655952838036` ("Cantidad de problems AR") con valor distinto de vacío/`0_problems_ar`.
   - `38655997571348` ("Cantidad de issues AR") con valor distinto de vacío/`0_issues_ar`.
2. **Notas internas con URL GitHub** — regex sobre comments internos:
   - `r"github\.com/TiendaNube/(Issues|Problems)/issues/(\d+)"`
3. **Notas internas con "+1" reportado** — regex:
   - `r"\+1\s+(issue|problem)\s+reportado"`

**No usar `s__tech__ticket_issue_problem__event` como fuente única** — está vacía para tickets que sí tienen I/P (verificado en 7239962).

### 4.4 Cierre coherente (Resolved / Snoozed)

| | |
|---|---|
| **RC positiva (0)** | Cierre del ticket coherente con el flujo: resolved o snoozed cuando corresponde. |
| **RC negativa (1)** | Cierre directo sin justificación coherente. |
| **Aplica si** | El ticket se cerró. |
| **N/A si** | El ticket sigue abierto. |
| **Señales** | `tickets_events` con transición final. |
| **LLM** | No (depende de la heurística actual). |
| **Estado código** | Ya implementado: sub-regla `cierre_coherente` (Fase 1). |
| **Gap** | Revalidar con el caso 7239962 — el ticket eventualmente se cerró a las 12:04 del 07/04 con `solved`, lo cual es correcto. La sub-regla puede haberlo tomado bien. |

**Nota operativa de Sofía:** *"El snooze es un resuelto puertas adentro, la diferencia que tiene con resuelto es que retrasa el envío de CSAT 2hs. A veces es muy utilizado en este tipo de casos donde hay un error (bug) o problem (oportunidad de mejora) en el sistema."*

**Implicancia:** la sub-regla 4.4 NO debe penalizar snooze cuando hay I/P asociado — es el uso correcto. Validar que el código actual de `cierre_coherente` ya contempla esto, y agregar test si no.

---

## Constantes que el código va a necesitar

```python
# Macros "Issue no triaged" (hold por I/P)
MACRO_ISSUE_NO_TRIAGED = {
    "AR": 1900012469807,   # [AR/LT] Issue:: Issue no triaged | Nuevo [Jul 2025]
    "BR": 5249935349524,   # [BR] Issue:: Issue não triaged | Novo
}

# Custom fields I&P (Zendesk API, NO en lake)
FIELD_CANTIDAD_PROBLEMS_AR = 38655952838036
FIELD_CANTIDAD_ISSUES_AR = 38655997571348

# Regex para detectar I/P en notas internas
REGEX_GITHUB_IP = re.compile(
    r"github\.com/TiendaNube/(Issues|Problems)/issues/(\d+)", re.IGNORECASE
)
REGEX_PLUS_ONE = re.compile(
    r"\+1\s+(issue|problem)\s+reportado", re.IGNORECASE
)
```

---

## Tickets pedagógicos

- **7239962** — cubre 4.1 y 4.3 en el mismo ticket. Calibración recomendada para ambas.
  - 4.3 negativa: el 5/4 14:35:49 pasó a hold sin la macro `1900012469807`. Custom fields `1_issue_ar` y `1_problem_ar` poblados (señal de I/P), URLs de GitHub `issues/36234` y `Problems/422` en notas internas.
  - 4.1 negativa: el 6/4 14:14:05 Rosario marcó pending después de un comment completo explicando que es un Problem reportado (sin pregunta de sondeo). Debió ser solved o snooze.
- **7189367** (de calibraciones previas, en memoria) — sigue siendo referencia para 4.2 (hold con SD correctamente abierta).

---

## Estado código vs discovery

| Sub-regla | Código existente | Cobertura del discovery |
|---|---|---|
| 4.1 Pending LLM | [estado_pending_llm.py](robia_procesos/reglas/estado_pending_llm.py) | Sin tests. Falta calibrar prompt con 7239962. |
| 4.2 Hold sin SD | `hold_sin_side_conversation` en [reglas/__init__.py](robia_procesos/reglas/__init__.py) | Fix de `sd_parent_ticket_id` ya aplicado. ✅ |
| 4.3 Hold con I/P | Sin código | Patrón cerrado, listo para implementar. |
| 4.4 Cierre coherente | `cierre_coherente` (Fase 1) | Revalidar que contempla snooze como cierre válido con I/P. |

---

## Costo en queries / tokens

- **Lake:** `tickets_events` (status timeline) + `macros_usage` por ticket. Baratísimo, ya en uso.
- **Zendesk API:** `get_ticket` (custom fields I/P) + `get_ticket_comments` (regex GitHub/+1). Compartido con otros procesos vía cache.
- **OpenAI:** solo para 4.1 (clasificar respuesta completa vs pregunta de sondeo).
