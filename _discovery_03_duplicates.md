# Discovery — Proceso 3: Duplicates

**Fecha cierre:** 2026-05-27
**Calibrado contra:**
- 7069887 (3.4 fusión leaked en público)
- 7364712 / 7365485 (3.2 macro de duplicado no aplicada)
- 6891651 / 6891653 (3.5 NUEVA: no priorizó WA)
- 7362139 (caso positivo — WA priorizado)
- 7124125 (caso positivo — más antiguo priorizado, 3 duplicados fusionados)

**Estado:** ✅ cerrado.

---

## Definición operativa

Detectar si el guru identificó correctamente tickets duplicados del mismo merchant y los fusionó siguiendo las reglas operativas: (a) priorizar WhatsApp, (b) si no hay WA, fusionar al más antiguo, (c) aplicar macro de cierre antes de fusionar, (d) que la nota de merge quede como interna.

**Encadenamiento con proceso 2 (Id Usuario/Org):** *"El historial de conversación se actualiza siempre que el ticket tenga organización asociada"*. Si el guru no asoció org (2.2 negativa), no le aparece el historial → no detecta el duplicado. Hay dependencia natural 2.2 → 3.1.

---

## Detección base — Identificar el ticket "kept" y sus duplicados cerrados

Zendesk emite automáticamente un comment al fusionar tickets. **El texto del comment es BR-PT fijo** (independientemente de la geografía de la cuenta), confirmado en 7069887 (AR), 7362139 (AR/WA) y 7124125 (AR/email):

```
A solicitação #NNNNNNN "..." foi fechada e fundida com esta solicitação.
Último comentário na solicitação #NNNNNNN: ...
```

**Regex de detección:** `r"A solicitação #(\d+).*?foi fechada e fundida com esta solicita"`

Cuando se detecta este comment en un ticket:
- Este ticket es el **kept** (conservado).
- Los `#NNNNNNN` extraídos son los **cerrados** (duplicados).
- `public=True` → leak (sub-regla 3.4).
- `public=False` → correcto (caso esperado).

---

## Las 5 sub-reglas (4 originales + 1 nueva)

### 3.1 No detectó un caso duplicado

| | |
|---|---|
| **RC positiva (0)** | Si existía un duplicado, lo detectó y fusionó. |
| **RC negativa (1)** | Existían tickets relacionados del mismo merchant en ventana cercana y no se fusionaron. |
| **Aplica si** | El ticket tiene `organization_id` asociado (precondición — sin org no hay historial). |
| **N/A si** | El merchant no tiene otros tickets en ventana ±N días. |
| **Señales** | Zendesk API: `/api/v2/search.json?query=requester:{email} type:ticket` o `?query=organization:{id}` con filtro temporal ±48h. Comparar subject/topic. LLM puede ayudar con similitud semántica de body. |
| **LLM** | Sí, para validar similitud semántica entre candidatos. |
| **Gap** | Definir ventana temporal canónica (sugerido ±48h, calibrar) y umbral de similitud. |

### 3.2 No aplicó la macro de duplicado antes de fusionar

| | |
|---|---|
| **RC positiva (0)** | Antes del cierre del ticket duplicado, se aplicó la macro `[AR] Acción:: Cerrar conversa duplicada` (`35553003184020`) o `[BR] Ação:: Fechar conversa duplicada` (`15965574871828`). |
| **RC negativa (1)** | El ticket fue cerrado por fusión sin aplicar la macro correspondiente. |
| **Aplica si** | El ticket fue cerrado por merge (es un ticket "cerrado" referenciado en el comment de otro ticket). |
| **N/A si** | El ticket no se cerró por fusión. |
| **Señales** | 1) Detectar `kept` por regex en el ticket analizado. 2) Para cada `#duplicado` extraído: query `macros_usage WHERE ticket_id=duplicado` y verificar si alguno de los macro_id es `35553003184020` o `15965574871828`. |
| **LLM** | No. Determinístico. |
| **Gap** | Ninguno. Validado contra 7365485 (no aplicó). |

### 3.3 No fusionó al ticket más antiguo

| | |
|---|---|
| **RC positiva (0)** | El ticket conservado (kept) es más antiguo que los duplicados cerrados. |
| **RC negativa (1)** | El ticket conservado es más nuevo que algún duplicado cerrado, y ninguno de la pareja es WA. |
| **Aplica si** | Hay merge detectado, y ningún ticket de la pareja es WhatsApp (si hay WA, prevalece 3.5 sobre 3.3). |
| **N/A si** | No hay duplicado o hay WA en la pareja. |
| **Señales** | Comparar `created_at` del kept vs created_at de cada `#duplicado` (Zendesk API). |
| **LLM** | No. |
| **Gap** | Ninguno. Validado contra 7124125 (más antiguo conservado + 3 duplicados más nuevos cerrados). |

### 3.4 Dejó el detalle de la fusión en respuesta pública

| | |
|---|---|
| **RC positiva (0)** | El comment de notificación de merge está como `public=False` (nota interna). |
| **RC negativa (1)** | El comment de merge está como `public=True`. |
| **Aplica si** | Hay merge detectado en el ticket. |
| **N/A si** | El ticket no es kept de un merge. |
| **Señales** | El mismo comment detectado por la regex base, leyendo el flag `public`. |
| **LLM** | No. |
| **Gap** | Ninguno. Validado contra 7069887 (público) vs 7362139/7124125 (interno). |

### 3.5 NUEVA — No priorizó WhatsApp en la fusión

| | |
|---|---|
| **RC positiva (0)** | Si entre los duplicados hay un ticket con `via.channel=whatsapp`, ese ticket es el `kept`. |
| **RC negativa (1)** | Existía un duplicado WA pero la fusión se hizo en sentido opuesto (kept es no-WA, duplicado es WA). |
| **Aplica si** | Hay merge detectado y al menos un ticket de la pareja tiene `via.channel=whatsapp`. |
| **N/A si** | Ningún ticket de la pareja es WA. |
| **Señales** | `get_ticket(kept).via.channel` y `get_ticket(duplicado).via.channel`. |
| **LLM** | No. |
| **Gap** | Ninguno. Validado contra 6891651/6891653 (negativo) y 7362139 (positivo). |

**Sugerencia para el Sheet `RCs para Procesos`:** agregar la fila 3.5 con:
- RC Positiva: *"Fusionó priorizando WhatsApp cuando había canal WA en la pareja"*
- RC Negativa: *"No fusionó al ticket de WhatsApp pudiendo hacerlo"*
- Ejemplo: *"Existían 2 tickets duplicados (uno por WA, otro por otro canal) y el guru fusionó al no-WA"*
- Sub-regla: `duplicate_no_prioriza_whatsapp`

---

## Algoritmo unificado

Para cada ticket analizado:

```
1. Leer comments del ticket via Zendesk API.
2. Aplicar regex de merge sobre cada comment.

3. Si NO matchea ningún comment:
     → No es un ticket kept de merge.
     → Para 3.1: buscar candidatos via /api/v2/search.json?query=requester:X type:ticket
       (filtrar por ventana temporal y comparar subject/topic; LLM para validar similitud).
     → Sub-reglas 3.2/3.3/3.4/3.5 emiten NO_EVALUABLE.

4. Si matchea (este ticket es kept):
     - Extraer #NNNNNNN del comment (regex group 1) → lista de duplicados.
     - 3.4 (público): ¿el comment es public=True? → RC negativa si sí.
     - Para cada #duplicado:
         - get_ticket(#duplicado) para tener created_at y via.channel.
         - 3.2 (macro): macros_usage del #duplicado contiene 35553003184020 o 15965574871828?
         - 3.3 (más antiguo): comparar created_at kept vs duplicado (solo si ninguno es WA).
         - 3.5 (WA): si duplicado es WA y kept no es WA → RC negativa.
```

---

## Constantes que el código va a necesitar

```python
MACRO_CERRAR_DUPLICADO = {
    "AR": 35553003184020,
    "BR": 15965574871828,
}

REGEX_MERGE_NOTIFICATION = re.compile(
    r"A solicita[çc][ãa]o #(\d+).*?foi fechada e fundida com esta solicita",
    re.IGNORECASE | re.DOTALL,
)

VENTANA_DUPLICADO_HORAS = 48   # para 3.1, calibrar con casos reales
```

---

## Costo en queries / tokens

- **Lake:** 1 query `macros_usage` por cada ticket duplicado referenciado (típicamente 1-3 por kept). Barato.
- **Zendesk API:**
  - 1 `get_ticket` + 1 `get_ticket_comments` por ticket analizado (compartido con otros procesos si cacheamos).
  - 1 `get_ticket` adicional por cada duplicado referenciado (para sus created_at + via.channel).
  - 1 `/api/v2/search.json` solo para 3.1 (la única que requiere búsqueda).
- **OpenAI:** solo para 3.1 (similitud semántica de tickets candidatos). Las otras 4 son determinísticas.

---

## Tickets pedagógicos

- **7069887** (3.4 negativo) — merge notification quedó `public=True`.
- **7364712 / 7365485** (3.2 negativo) — en 7365485 (cerrado por merge) NO se aplicó `35553003184020`. Lo que se aplicó fue una macro de derivación (`45429145129108` = `[AR] Acción:: Derivar para tópico PN`). Confunde a primera vista — vale recordar que la macro de derivación NO sustituye la de cierre duplicado.
- **6891651 / 6891653** (3.5 negativo) — el guru fusionó al no-WA en lugar de al WA.
- **7362139** (positivo, WA) — kept es WA, duplicado #7363132 fusionado bien.
- **7124125** (positivo, antigüedad) — kept es el más antiguo, 3 duplicados (#7125600, #7125609, #7166655) fusionados bien.
