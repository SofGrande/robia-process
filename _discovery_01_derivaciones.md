# Discovery — Proceso 1: Derivaciones

**Fecha cierre:** 2026-05-27
**Calibrado contra:** tickets 7448501 (triagem humana) y 7511698 (ADA)
**Estado:** ✅ cerrado, listo para implementación cuando llegue su turno.

---

## Definición operativa

Evaluar si el ticket terminó en el equipo correcto en cada momento de su ciclo de vida, considerando 4 actores posibles de derivación:

1. **Triagem humano** — un guru asignado al grupo Triagem aplica macro de derivación.
2. **Guru asignado** — el guru que tiene el ticket lo deriva a otro equipo.
3. **ADA / AI Agent** — el asistente automático deriva sin intervención humana.
4. **Trigger Zendesk** — cambio de grupo por regla automática de Zendesk sin macro.

---

## Las 4 sub-reglas

### 1.1 Triagem derivó al equipo correcto

| | |
|---|---|
| **RC positiva (0)** | Triagem derivó al equipo coherente con el contenido del ticket. |
| **RC negativa (1)** | Triagem derivó al equipo equivocado. |
| **Aplica si** | Hay cambio de `group_id` con macro `"[AR] Acción:: Derivar para tópico %"` o `"[BR] Ação:: Derivar para %"` donde `author_id` NO es assignee del ticket. |
| **N/A si** | El ticket entró directo a un equipo final sin pasar por Triagem (raro). |
| **Señales** | `tickets_events` (cambio group_id) + `macros_usage` (macro + author_id) + `assignment` (lista de assignees del ticket) + Zendesk API `get_ticket` (subject+description). |
| **LLM** | Sí — para decidir si el equipo destino era correcto para el contenido. |
| **Gap** | Catálogo de macros con su `nombre` (Zendesk API `list_macros` o `search_macros`). |

### 1.2 Guru derivó al equipo correspondiente cuando aplicaba (caso Dagmara)

| | |
|---|---|
| **RC positiva (0)** | Surgió la necesidad de derivar y el guru aplicó la macro correcta. |
| **RC negativa (1)** | Cerró sin derivar / derivó sin macro / derivó al equipo equivocado. |
| **Aplica si** | Hay cambio de `group_id` con macro `Derivar para %` donde `author_id` SÍ es un assignee del ticket. |
| **N/A si** | El caso no requería derivación. |
| **Señales** | Igual que 1.1. |
| **LLM** | Sí — para "¿el equipo destino era el correcto?". |
| **Gap** | Catálogo de macros. |

### 1.3 Automatización derivó bien (ADA)

| | |
|---|---|
| **RC positiva (0)** | ADA derivó al equipo coherente con el contenido del ticket. |
| **RC negativa (1)** | ADA derivó al equipo equivocado. |
| **Aplica si** | Hay cambio de `group_id` durante la ventana de un registro de `assignment` con `guru_name='AI Agent'` (assignee_id=49018376478100, group_id=-1) y sin macro asociada. |
| **N/A si** | El ticket no pasó por ADA. |
| **Señales** | `assignment` (ventana de AI Agent) + `tickets_events` (cambio group_id) + Zendesk API `get_ticket`. |
| **LLM** | Sí — para "¿el equipo destino era el correcto?". |
| **Gap** | Ninguno. |

### 1.4 Guru derivó cuando correspondía (no se quedó trabajándolo)

| | |
|---|---|
| **RC positiva (0)** | El caso pertenecía a otro equipo y el guru derivó. |
| **RC negativa (1)** | El caso pertenecía a otro equipo pero el guru lo trabajó internamente. |
| **Aplica si** | El último equipo asignado al cierre del ticket y el contenido del ticket sugieren diferentes equipos. |
| **N/A si** | El equipo asignado al cierre matchea el contenido. |
| **Señales** | Último `group_id` del ticket + ausencia de macros `Derivar para %` aplicadas por el assignee final + Zendesk API `get_ticket`. |
| **LLM** | Sí — para determinar el equipo correcto a partir del contenido. |
| **Gap** | Ninguno. |

---

## Patrón general para identificar el actor

```
Para cada cambio de group_id en tickets_events:

  1. ¿Hay registro de assignment con guru_name='AI Agent' (assignee_id=49018376478100)
     cuya ventana [assignment_start_time, assignment_end_time] contiene este timestamp?
       → ADA derivó (1.3)

  2. ¿Hay macro en macros_usage con timestamp ±1s del cambio
     cuyo nombre matchea "Derivar para %"?
       Sí:
         - ¿author_id de la macro está en lista de assignees del ticket?
             Sí → guru derivó (1.2)
             No → triagem humano derivó (1.1)
       No → trigger Zendesk puro (sin sub-regla específica, queda en logs)

```

---

## Conteo de errores (regla Sofía)

Si triagem se equivocó (1.1 negativa) Y el guru no derivó después (1.4 negativa), el ticket suma **2 errores** (no 1). La auditoría manual de Sofía los cuenta separados.

---

## Constantes que el código va a necesitar

```python
ADA_ASSIGNEE_ID = 49018376478100
ADA_GROUP_ID = -1

TRIAGEM_GROUPS = {
    "AR": 4416857078676,
    "BR": 1900001463447,
    "LATAM": 7203714749716,   # "[MX] To Assign"
}

MACRO_PREFIJO_DERIVACION = {
    "AR_LATAM": "[AR] Acción:: Derivar para tópico ",
    "BR": "[BR] Ação:: Derivar para ",
}
```

---

## Limitación operativa descubierta

**El ETL del lake atrasa ~24h respecto a Zendesk.** Confirmado con `MAX(ticket_id)=7514404` y `MAX(event_timestamp)='2026-05-27 00:30'`, mientras que Zendesk ya tenía tickets 7517+. RobIA Procesos solo puede evaluar tickets con ≥24h de antigüedad.

Para auditorías semanales (workflow Sofía), OK. Para monitoreo en tiempo real, no.

---

## Ticket de referencia pedagógico

**7448501** queda como caso canónico de calibración:
- Pasó por Triagem AR (group 4416857078676).
- Derivado a `[AR] PN - Riesgo y activación SMBs` (group 44818795141780) por un guru de Triagem aplicando la macro `45429138530708`.
- Rotó entre varios gurus (David, Pedro C., Leandro, Micaela) en el equipo final.
- Tiene también casos de cierre, hold con SD, status changes — útil para los otros 3 procesos también.

**7511698** queda como caso canónico de ADA:
- ADA tomó el ticket → 1.5 min después derivó automáticamente a `[AR] PN - Riesgo y activación SMBs` sin macro.
- Guru humano (Leandro R.) toma el ticket 22 min después.
