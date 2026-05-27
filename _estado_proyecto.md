# RobIA Procesos — Estado del Proyecto

**Última actualización:** 2026-05-08
**Owner:** Sofía Grande (sofia.grande@tiendanube.com)
**Repo de trabajo:** https://github.com/SofGrande/robia-process (privado)
**Repo padre planificado:** https://github.com/TiendaNube/robia-qa

---

## Resumen ejecutivo

RobIA Procesos es el **tercer pilar del IQS** automatizado de calidad CX, complementando a RobIA Soft Skills (subjetivo, GPT-4o) y RobIA Solución Asertiva (binario, en construcción por Robs). Procesos cubre el bloque **Crítico para el Negocio** de la guideline IQS: 4 categorías y 10 procesos auditables.

Estado actual:
- **8 sub-reglas implementadas** (de 45 totales identificadas), 64 tests verdes, smoke test contra el lake funcionando.
- **Discovery completo de las 4 categorías** con definiciones canónicas validadas con Sofía.
- **Catálogo de R/Cs creado** en el Master Track (`RCs para Procesos`, hoja nueva).
- **33 sub-reglas pendientes** con fuente identificada (LLM, Zendesk API, GitHub API, Slack, lake).
- **4 sub-reglas marcadas humanas** (catch-all).

El siguiente paso es la **implementación en 3 fases** post-discovery (definidas con Sofía 2026-05-08):

- **Fase 0** — Adaptar el evaluador al modelo "por guru × proceso" (output compatible con la planilla de auditorías).
- **Fase A** — Conexiones a fuentes externas en paralelo: Zendesk API, OpenAI, GitHub API, Slack API/MCP, Stats API, lake discovery.
- **Fase B** — Evaluaciones por categoría en orden: 1° Clasificación → 2° Procesos Zendesk → 3° Issues & Problems → 4° Knowledge Base. Cada categoría como bloque cerrado.
- **Fase C** — Integración al repo padre `TiendaNube/robia-qa` + calibración continua.

Plan operativo detallado en el Master Track de Sofía y resumido en la Sección 5 de este doc.

---

## 1. Discoveries

### 1.1 Contexto del producto

**Tres pilares del IQS** automatizado:
1. **RobIA Soft Skills** — evaluación subjetiva (Abertura, Tom, Personalização, Empatia, Clareza, Estructura Piramidal, Continuidade) con GPT-4o, 2 stages (score + reasoning). Live, mantenido por Roberta Alves.
2. **RobIA Solución Asertiva** — 3 criterios binarios (Análise & Sondagem, Autonomía, Resposta Correta) con GPT-4o + Pinecone retrieval sobre KB. Prompts v1 listos, runtime pendiente. Mantenido por Roberta Alves.
3. **RobIA Procesos** — 4 categorías / 10 procesos / 45 sub-reglas binarias contra eventos del lake + APIs externas. Owner: Sofía Grande. **Este proyecto.**

**Pipeline actual** (en `TiendaNube/robia-qa`):
```
Sampler (sampler_robia.py) → amostra_BR_S22.json → Analyzer Soft Skills → Google Sheets
                                                ↓
                                   manual rows (hoy 100% Crítico para el Negocio = humano)
```

Procesos viene a automatizar la última caja. Ya está reservado en el roadmap del repo padre como **"Expansão 1 — Processos via eventos do Zendesk"**.

**Decisión de integración (2026-05-08):** convivir, no rama paralela. Hoy desarrollo en `SofGrande/robia-process`; cuando esté maduro, PR a `TiendaNube/robia-qa` como módulo nativo.

### 1.2 Definiciones canónicas

Estas definiciones operativas son **oro para los prompts LLM**. Vienen directo de Sofía, capturadas durante calibración. Se usan literales en system prompts.

#### Naturaleza de la conversa

| Naturaleza | Definición operativa |
|---|---|
| **Issue** | Reportar un bug (algo está roto en la plataforma). |
| **Problem** | Reportar oportunidad de mejora. *"Siempre que decimos que NO TENEMOS algo es Problem."* |
| **Request** | Hacer una acción en la tienda o cuenta del merchant. |
| **Duda Investigativa** | Investigar abriendo herramientas (stats, admin, etc.). Ej: error al cargar producto, problema con pago. |
| **Duda Autoatención** | Resolver con macro / KB / tutorial sin investigar. |

Heurística decisiva: *si el guru tuvo que abrir una herramienta → Investigativa; si no → Autoatención. Si "no tenemos algo" → Problem.*

#### Subtópico

> **Subtópico = lo que pide el merchant, lo que necesita.**

NO es el tema técnico ni las palabras clave. Es la **intención** del merchant. Un merchant que escribe sobre un error de pago tiene tema técnico "pago" pero el subtópico depende de qué pide (resolver el error vs reembolso vs explicación).

#### Estado Pendiente

> *"El estado pendiente se define cuando el guru necesita si o si una respuesta del merchant a su duda para poder avanzar con la resolución. Si no hay una pregunta clave de sondeo en su mensaje o un action item claro para que el merchant avance respondiendo, el estado pendiente está mal aplicado."*

#### Duplicates — regla de oro

- **Duplicado**: mismo merchant, misma organización, hablan de lo mismo.
- **Detección manual del guru**: (a) historial del ticket, (b) búsqueda por organización, (c) búsqueda por email de registro.
- **Reglas de fusión** (en orden):
  1. Si hay canal WhatsApp entre los duplicados → priorizar WA.
  2. Si no hay WA y conviven MSG/Email → fusionar al **más antiguo**.
  3. **Antes de fusionar**, aplicar macro `[AR] Acción:: Cerrar conversa duplicada` (AR/LATAM) o `[BR] Ação:: Fechar conversa duplicada` (BR).

#### Equipos macro de TS (Tech Support)

Los 3 valores de `s__tech__ticket_subdomains__event.domain` son los 3 equipos de TS:
- **consumers** — atención a compradores
- **merchants** — atención a comerciantes
- **ecosystem** — atención a partners/apps

Cada equipo resuelve aspectos distintos del admin del merchant. Las side conversations típicas se abren entre estos equipos.

#### Macros de derivación

Existe un catálogo con prefijo `Derivar para {equipo}` (AR/LATAM, equivalente PT-BR a confirmar). Caso canónico: ticket en grupo Pago Nube + merchant pregunta por contraseña → guru de Pago Nube debe aplicar macro `Derivar para Account` para devolver a Bido/Account.

### 1.3 Lake Databricks (`data_products_prd.data_cx`)

13 tablas relevantes confirmadas durante el discovery:

| Tabla | Para qué sirve |
|---|---|
| `s__general__zendesk_assignment__event` | Asignaciones (group_id, guru_name, type) — fuente de geografía vía `guru_name` |
| `s__general__zendesk_chats__event` | Chats con `actor_type` (agent/end-user/system/trigger) |
| `s__general__zendesk_interactions__event` | Interacciones in/out, `interaction_timestamp`, source (Chat/Comment/VoiceComment), `author_id`, `author_type` |
| `s__general__zendesk_macros_usage__event` | Macros aplicadas por ticket (7066 distintas en el catálogo) |
| `s__general__zendesk_satisfaction_score__event` | CSAT |
| `s__general__zendesk_sla_target__event` | SLA |
| `s__general__zendesk_ticket_nature__event` | Naturalezas marcadas (8 valores con aliases ES↔PT) |
| `s__general__zendesk_ticket_topics__event` | Triplas main/sec/sub (3203 subtópicos en catálogo) |
| `s__general__zendesk_tickets_events__event` | Cambios de campo (status, group_id, assignee_id, sla, satisfaction_score) — timeline |
| `s__human__csat__event` | CSAT humano |
| `s__tech__ticket_custom_fields__event` | Custom fields (poco poblado en muestra — `support_feedback`, `type_of_task`) |
| `s__tech__ticket_issue_problem__event` | Vinculación con Issues/Problems (type, number, url) |
| `s__tech__ticket_subdomains__event` | Dominios de TS (consumers/merchants/ecosystem) |

**Vista agregada:**
| Vista | Notas |
|---|---|
| `g__general__side_conversations__agg_ticket` | Side conversations. **Crítico:** `sd_ticket_id` es la SD en sí (ticket nuevo); `sd_parent_ticket_id` es el ticket padre — **filtrar por este último** |

**Bug histórico encontrado y arreglado** (2026-05-08): la sub-regla `hold_sin_side_conversation` consultaba la vista por `sd_ticket_id` en vez de `sd_parent_ticket_id`, generando falsos positivos. Detectado durante calibración del 7189367 (Belén abrió SD que existía en lake pero no aparecía).

**Detección de geografía**: vía `guru_name` del último assignment.
- `... de Tiendanube` → LATAM (incluye AR/MX)
- `... da Nuvemshop` → BR
- `Agente Virtual AR` / `Agente Virtual BR - Claudia`
- Hay legacy sin patrón ("Adrian", "Ale") → DESCONOCIDA

**Tablas no descubiertas en `data_cx` pero referenciadas en roadmap robia-qa**:
- `ticket_metric_events` y `ticket_audits` — no están en `data_products_prd.data_cx`. Probablemente otro esquema (`bronze`, `raw_zendesk`, etc.). A descubrir.

### 1.4 Sheet maestro de Tópicos/Naturaleza

**ID:** `1OToNB4aEe5n5ciD--NngBCrUkWCdr6AoH_NyDZ2p63Y`
**Título:** "[Global] [CX] Tópicos/Subtópicos List"
**Compartido con:** `robia-qa-pipeline@support-468213.iam.gserviceaccount.com` (lectura)

Hojas relevantes:

| Hoja | Estructura |
|---|---|
| `AR_Tópicos_Zendesk/Slack` | Header fila 2. Cols: 0=Equipo, 1=TP, 2=TS, 3=Sub-tópico |
| `LATAM_Tópicos_Zendesk/Slack` | Header fila 1. Sin Equipo. Cols: 0=TP, 1=TS, 2=Sub-tópico |
| `[BR] Tópicos Zendesk/Slack` | Header fila 1. Cols: 0=Equipe, 1=TP, 2=TS, 3=Produto, 4=Derivar para, 5=Subtópico |
| `[LATAM] Naturaleza da Conversa` | Datos desde fila 9, col 1. 6 valores ES |
| `[BR] Natureza da Conversa` | (¡atención: "Natureza", no "Naturaleza"!). Datos fila 9, col 1. 6 valores PT-BR |
| `[AR] Ruteo Nube y TS` | Header fila 0. Mapping equipo → tags Zendesk |
| `[BR] Ruteo Nube e TS` | Idem para BR |

Total combinado: ~2243 combinaciones (main, sec, sub) y ~2012 subtópicos únicos.

### 1.5 Master Track de Calidad

**ID:** `1foZR6bNgRrK7vYs2ZWdvf1IrBE-Dext068iZDl7TSsA`
**Título:** "[Support] Calidad&CI - Master Track"

Hojas de catálogo de R/Cs:
| Hoja | Cols | Estado |
|---|---|---|
| `RCs para Soft` | 6 | Existente (formato Soft Skills, 3 bandas: 9-10 / 7-8 / 0-6) |
| `RCs para Sol.` | 5 | Existente (formato Sol. Asertiva, RC Positiva / Negativa) |
| `RCs para Procesos` | 6 | **Creada 2026-05-08** — formato adoptado: `Categoria \| Proceso \| Evento detectado \| RC 0 \| RC 1 \| Sub-regla evaluador` |

**Decisión de scoring (2026-05-08):** Procesos es **binario (0/1)**, alineado con Sol. Asertiva. Razón: la guideline IQS para "Crítico para el Negocio" es Thumbs Up/Down. El evaluador detecta hechos discretos.

### 1.6 Planilla de Auditorías ([CX Ops] Auditorias de Qualidade 2026)

**ID:** `1fFWwtVi7GOqXm3Yu3UEhWdF2-7KXzCSHjBUc7VFHrPQ`

Donde Sofía registra hoy auditorías manuales:
- Hojas `[BR] Manual`, `[AR] Manual`, `[LT] Manual`
- Cols U-AN: 10 procesos × 2 cols (proceso + R/C en desplegable)
- Cols AW-BO: conteo de errores
- Cols BQ-CI: conteo de aciertos excluyendo N/A

Los procesos U-AN coinciden 1:1 con los 10 procesos del CSV `RCs para Procesos`. Cuando RobIA Procesos esté operativo, esas columnas quedan **deprecadas** (alimentadas por el evaluador, no a mano).

**4 estados visibles en la planilla:**
- 🟢 (correcto) — `0` interno
- 🔴 (error) — `1` interno
- `N/A` — el proceso no aplica al ticket (juicio humano)
- Celda vacía — no_evaluable (datos insuficientes; el auditor decide manualmente)

### 1.7 Modelo de evaluación: por guru

Definido en `docs/GURU_TEAM_LOGIC.md` del repo padre:
- **Hoy (mono-guru):** el guru evaluado = **último assignee** del ticket. Lógica: "quien fica con el ticket en la mano en el cierre es responsable de la percepción final".
- **Backlog del repo padre:** multi-guru scoring (atribución por tramo a cada guru). Cuando llegue, RobIA Procesos lo adopta.
- `EXCLUDED_GURUS` (gestores/TLs en `config_metas.json`): tickets cerrados por ellos se descartan.
- Cross-country prohibido: rodada BR audita solo gurus BR, etc.

---

## 2. Qué hicimos hasta el momento

### 2.1 Fases 1-3 completadas (código en repo `SofGrande/robia-process`)

**Fase 1 — Contrato + 1 regla** (commit `59df6a3` parte de):
- Estructura del paquete `robia_procesos/` con `core/contrato.py`, `core/db.py`, `cli.py`.
- Sub-regla `cierre_coherente`.

**Fase 2 — Reglas directas + Catálogo Sheets**:
- Sub-reglas `pending_post_solved_sin_trigger`, `topico_completo`, `topico_combinacion_valida`, `naturaleza_completa`, `naturaleza_valida`.
- Loader del Sheet maestro de Tópicos con cache CSV local (`_cache_topicos.csv`, `_cache_naturalezas.csv`).
- Normalización tolerante (sin tildes, sin separadores, lowercase) y aliases ES↔PT.

**Fase 3 — Reglas multi + Geografía** (commit `48c4d01` incluido):
- Sub-reglas `hold_sin_side_conversation`, `multitopico_todas_validas`, `multinaturaleza_todas_validas`, `topico_geografia_consistente`.
- Módulo `core/geografia.py` (parser de `guru_name`).
- Catálogo extendido para soportar filtrado por geografía.

**Tests:** 64/64 unitarios verdes (sin tocar lake ni Sheet).

### 2.2 Calibración del ticket 7189367 (2026-05-08)

Calibración manual contra el ojo de Sofía como auditora. Hallazgos:

1. **Bug detectado y arreglado**: `hold_sin_side_conversation` daba falso positivo. Belén M. había abierto side conv "Consulta KYC" hacia [AR] Ops Pago Nube el 26/03 que se resolvió el 06/04. Los 2 tramos largos de hold (~4 días cada uno) caían dentro del período de la SD → uso correcto. La query usaba columna equivocada. Fix: `sd_ticket_id` → `sd_parent_ticket_id`.

2. **Hallazgos legítimos del evaluador en el smoke test:**
   - Ticket 7189367 multitópico: tripla `Pago Nube / Kyc / Validacion De Identidad` no figura en hoja maestra Doc&Comm → posible falta en catálogo.
   - Ticket 7270214 multitópico: 2 de 3 triplas no documentadas (`Tópicos Especiales / Otros... / Sin Acceso Wa`, `Others / Others / General Others-ar`) → drift entre Zendesk y el Sheet maestro. Reportar a Doc&Comm.

3. **Caso pedagógico de Derivações (no automatizado todavía)**: Dagmara cerró el ticket directamente cuando debió haber devuelto a Online (que tenía issue abierto sin triage) con macro de derivación. Es exactamente el tipo de caso que detectaría la sub-regla `derivacion_correcta_pago_nube_online` (Fase 4).

### 2.3 Discovery completo de las 4 categorías

Definidos los **45 eventos detectables** (ver Sección 3 para detalle por categoría) con:
- Disparador concreto.
- Señal del lake o fuente externa requerida.
- Prompt esquemático cuando aplica LLM.
- Output (R/C exacta del catálogo).
- Riesgos y mitigaciones.

### 2.4 Hoja `RCs para Procesos` creada en Master Track

44 filas iniciales con formato:
```
Categoria | Proceso | Evento detectado | RC 0 (correcto) | RC 1 (error) | Sub-regla evaluador
```

Pendiente: Sofía retira la fila "Transportadora incorrecta" (descartada en discovery) y refina algunas frases de RC 0 según calibración futura.

### 2.5 Repo en GitHub

- `https://github.com/SofGrande/robia-process` (privado).
- 2 commits: estado base + fix side conv.
- `.gitignore` cubriendo credenciales, caches, dumps, smoke output.

---

## 3. Qué falta

### 3.1 Estado consolidado por categoría

| Categoría | Sub-reglas | Implementadas hoy | Fase 4 (con fuente identificada) | Humanas |
|---|---|---|---|---|
| Clasificación de la conversa | 10 | 5 | 5 (LLM + grupo embudo) | 0 |
| Procesos Zendesk | 21 | 3 | 16 | 2 |
| Issues & Problems | 6 | 0 | 5 (LLM + GitHub API) | 1 |
| Knowledge Base | 8 | 0 | 7 (Slack + Zendesk API + LLM) | 1 |
| **Total** | **45** | **8** | **33** | **4** |

### 3.2 Detalle por categoría

#### Clasificación de la conversa (10 sub-reglas)

**Tóp/Subtópico (6):**
- ✅ `topico_combinacion_valida` — Tripla no figura en hoja Doc&Comm
- ✅ `multitopico_todas_validas` — Multitópico con alguna fuera
- ✅ `topico_geografia_consistente` — Tripla en otra geografía
- 🟡 LLM — Faltan subtópicos esperables (multitópico incompleto)
- 🟡 LLM + Zendesk API — Macro dejó subtópico que no aplica
- 🟡 LLM — Subtópico 'General X' usado cuando hay específico

**Natureza (4):**
- ✅ `naturaleza_valida` — Naturaleza fuera del catálogo
- ✅ `multinaturaleza_todas_validas` — Multinaturaleza con alguna inválida
- 🟡 LLM — Falta naturaleza esperable
- 🟡 LLM — Naturaleza marcada que no matchea contenido

#### Procesos Zendesk (21 sub-reglas)

**Estado da Conversa (5):**
- ✅ `pending_post_solved_sin_trigger`
- ✅ `hold_sin_side_conversation`
- ✅ `cierre_coherente`
- 🟡 LLM — Pending mantenido con respuesta completa (prompt operativo definido)
- ⚪ humano — Otros usos incorrectos (catch-all)

**Duplicates (5):**
- 🟡 Lake + LLM — No detectó duplicado existente
- 🟡 Lake + Zendesk API — Fusionó pero sin macro pre-fusión
- 🟡 Lake — Fusión al ticket más nuevo
- 🟡 Regex/LLM — Comentario duplicate visible en respuesta pública
- 🟡 Regex/LLM — Nota de fusión visible

**Id Usuário/Org (3):**
- 🟡 Zendesk API — No fusionó usuario (caso WA con email)
- 🟡 Stats API — No asoció organización (BLOQUEADO sin acceso a stats)
- 🟡 Lake (custom fields) — No asoció ID partner

**Derivações (4):**
- 🟡 Lake + Hojas Ruteo — Triagem incorrecta
- 🟡 Lake + Zendesk API (catálogo macros) — Guru cerró sin derivar (caso Dagmara)
- 🟡 Zendesk API — Macro automática llevó a equipo incorrecto
- 🟡 LLM — Caso requería derivación pero el guru lo trabajó

**Side Conversation (4):**
- 🟡 Zendesk Guide API + LLM — SD abierta cuando info estaba en KB/GitHub
- 🟡 Regex/LLM — Template SD incompleto
- 🟡 Lake + LLM — SD a equipo incorrecto (consumers/merchants/ecosystem)
- ⚪ humano — Otros (catch-all)

#### Issues & Problems (6 sub-reglas)

- 🟡 GitHub API + LLM — A) No creó nuevo I/P (bug nuevo sin reportar)
- 🟡 Lake — B1) No reportó +1 al I/P existente (escenario tag/naturaleza presente)
- 🟡 GitHub API + LLM — B2) No reportó +1 (escenario "todo silencioso")
- 🟡 LLM — C1) Vinculó I/P sin necesidad (no era I/P)
- 🟡 Zendesk API + lake — C2) Vinculó I/P por macro sin aplicar
- ⚪ humano — D) Otros (catch-all)

#### Knowledge Base (8 sub-reglas)

**Aplicação (4):**
- 🟡 Zendesk API + LLM — No aplicó macro
- 🟡 Regex + Zendesk Guide API — Falta link del tutorial
- 🟡 Zendesk Guide API + LLM — No utilizó Zendesk Guide
- ⚪ humano — Otros (catch-all)

**Feedbacks (4):**
- 🟡 Slack API/MCP — Falto feedback BOT
- 🟡 **Lake puro** ✅ — Falto feedback stakeholders (cruce SD + custom field)
- 🟡 Slack API/MCP — Faltó feedback macros
- 🟡 Slack API/MCP — Faltó Feedback documentación

---

## 4. Requisitos para avanzar

### 4.1 Por categoría de sub-regla

#### Clasificación de la conversa
**Para las 5 sub-reglas LLM**:
- [x] OpenAI API key — **disponible**
- [x] Sheet maestro de Tópicos — **disponible**
- [ ] Mapping `group_id → equipo del ticket` (vía hojas `[AR/BR] Ruteo Nube y TS`) — para filtrar el catálogo de 2243 combinaciones a las relevantes por equipo
- [ ] Catálogo de macros con prefijos por equipo (Zendesk API) — para sub-regla "Macro dejó subtópico que no aplica"

#### Procesos Zendesk
**Para Estado (1 LLM faltante)**:
- [x] OpenAI API key

**Para Duplicates (5)**:
- [ ] **Tabla de merges/fusiones** en lake (no descubierta) — bloqueante
- [ ] Custom field de organización (a confirmar cuál es)
- [ ] Catálogo de macros pre-fusión vía Zendesk API

**Para Id Usuário/Org (3)**:
- [ ] **Stats API o equivalente** — a confirmar con DataOps/Infra (puede no existir)
- [ ] Custom fields completos descubiertos en el lake (organización, partner ID, partner checkbox)
- [ ] Zendesk API search de organizaciones por email

**Para Derivações (4)**:
- [ ] **Tabla de tags** del ticket en el lake (no descubierta) — bloqueante para detectar `issue-from-zapier` y similares
- [ ] Catálogo de **macros de derivación** vía Zendesk API (filtro `name LIKE 'Derivar para%'` AR/LATAM, equivalente PT-BR)
- [ ] Mapping `macro_id → equipo destino esperado`
- [ ] Hojas `[AR/BR] Ruteo Nube y TS` parseadas

**Para Side Conversation (3)**:
- [ ] **Zendesk Guide API** (KB search) — para detectar "info estaba en KB"
- [ ] Macro abridora de SD + template canónico (vía Zendesk API)
- [ ] LLM (OpenAI) — disponible

#### Issues & Problems
**Para las 5 sub-reglas Fase 4**:
- [ ] **GitHub API** a 2 repos (Issues + Problems) — necesito URLs y token de lectura
- [ ] Macros de I/P (que pegan +1 automático) vía Zendesk API
- [ ] Custom fields de I/P en formulario Zendesk (a descubrir)
- [ ] Detección de notas internas con URL de GitHub (regex sobre `interactions__event` filtrado a internas)
- [x] OpenAI API key

#### Knowledge Base
**Para Aplicação (3 sub-reglas)**:
- [ ] **Zendesk API** (catálogo de macros con prefijos por equipo) — disponible
- [ ] **Zendesk Guide API** (Centro de atención nube) — endpoint + scope
- [x] OpenAI API key

**Para Feedbacks (3 sub-reglas que requieren Slack)**:
- [ ] **Slack API o MCP** — token con scope `channels:history` o equivalente
- [ ] Lista de rooms a monitorear:
  - Room de feedback BOT (nombre exacto a confirmar)
  - Room de feedback macros (nombre exacto a confirmar)
  - `#support-documentação-feedback-ar` (existente)

**Para Feedback stakeholders (única sin Slack)**:
- [ ] Custom field específico de "feedback stakeholders" en `ticket_custom_fields__event` (a descubrir cuál es)

### 4.2 Pre-requisitos transversales

| Item | Estado | Bloquea |
|---|---|---|
| Lake discovery: tabla merges | Pendiente | Duplicates (5 sub-reglas) |
| Lake discovery: tabla tags | Pendiente | Derivações, I&P (5 sub-reglas) |
| Lake discovery: custom fields completos | Pendiente | Id Usuário/Org, I&P, Feedback stakeholders (5 sub-reglas) |
| OpenAI API key | ✅ Disponible | — |
| Zendesk API (macros + Guide) | Sofía tiene acceso, falta confirmar credenciales | Aplicação, Derivações, SD, Duplicates |
| GitHub API (Issues + Problems) | Pendiente: URLs de repos + token | I&P (5 sub-reglas) |
| Slack API/MCP | Pendiente | Feedbacks A/C/D (3 sub-reglas) |
| Stats API | Pendiente: confirmar con DataOps/Infra si existe | Id Usuário/Org sub-regla 2 |

---

## 5. Plan a ejecutar (Master Track)

Definido con Sofía el 2026-05-08. La lógica de orden es: **conexiones primero, evaluaciones después**. Cada categoría se ataca como un bloque cerrado, no sub-regla por sub-regla mezclada.

### Fase 0 — Base técnica del evaluador

| TASK | Tarea | % | HRS | Notas |
|---|---|---|---|---|
| RP-01 | Discovery completo de las 4 categorías (45 sub-reglas) | 100 | 30 | Cerrado con definiciones canónicas validadas |
| RP-02 | Crear hoja `RCs para Procesos` en Master Track Calidad | 100 | 2 | 43 filas iniciales |
| RP-03 | Doc Google "Estado del Proyecto" | 80 | 4 | Pendiente subir el .md a Google Doc |
| RP-04 | Adaptar el evaluador para entregar resultados por guru y por proceso | 0 | 10 | Refactor: pasa de "por ticket" a "una fila por guru × proceso", listo para pegar en planilla. Output del CLI compatible con tab `[XX] Manual` |

### Fase A — Conexiones a fuentes externas (en paralelo)

Las 6 conexiones se trabajan al mismo tiempo, no se bloquean entre sí. Total ~2 semanas.

| TASK | Tarea | % | HRS | Notas |
|---|---|---|---|---|
| RP-A1 | Conectar Zendesk API (macros + Centro de Atención / Zendesk Guide) | 0 | 6 | Necesario para casi todas las categorías |
| RP-A2 | Conectar OpenAI (configurar el cliente, ya hay key disponible) | 0 | 2 | Necesario para Clasificación, Estado LLM, I&P, KB |
| RP-A3 | Conseguir token GitHub + conectar a 2 repos (Issues + Problems) | 0 | 4 | Necesario solo para I&P |
| RP-A4 | Conectar Slack API/MCP a rooms BOT, macros, `#support-documentação-feedback-ar` | 0 | 6 | Necesario solo para 3 sub-reglas de Feedbacks |
| RP-A5 | Confirmar con DataOps/Infra acceso a Stats API | 0 | 2 | Si no existe, "No asoció organización" queda humana |
| RP-A6 | Buscar en Databricks tablas faltantes (fusiones, tags, custom fields) | 0 | 4 | Sesión técnica de exploración del lake |

### Fase B — Evaluaciones por categoría (en orden)

Cada categoría es un bloque cerrado. Se completa antes de pasar a la siguiente.

| TASK | Tarea | % | HRS | Notas |
|---|---|---|---|---|
| RP-B1 | **1° Clasificación** — Tópico/Subtópico + Naturaleza (10 sub-reglas) | 0 | 18 | 5 ya implementadas + 5 nuevas con IA. Usa OpenAI + Sheet maestro Tópicos + mapping `group_id → equipo` |
| RP-B2 | **2° Procesos Zendesk** — Estado + Duplicates + Id + Derivações + Side Conversation (21 sub-reglas) | 0 | 50 | 3 ya implementadas + 18 nuevas. Usa Zendesk API + lake (merges, tags, custom fields) + OpenAI. Bloque más grande |
| RP-B3 | **3° Issues & Problems** — bug nuevo, +1 existente, vinculación sin necesidad (6 sub-reglas) | 0 | 16 | Usa GitHub API + lake + OpenAI |
| RP-B4 | **4° Knowledge Base** — Aplicação (macros, tutoriales, Zendesk Guide) + Feedbacks (4 vías) (8 sub-reglas) | 0 | 28 | Usa Zendesk API + Slack + OpenAI. Feedback stakeholders es lake puro |

### Fase C — Integración y calibración

| TASK | Tarea | % | HRS | Notas |
|---|---|---|---|---|
| RP-C1 | Integrar RobIA Procesos al repo `TiendaNube/robia-qa` como módulo nativo | 0 | 8 | Output unificado en Google Sheets junto con Soft Skills y Solución Asertiva |
| RP-C2 | Calibración continua con muestras semanales y ajuste de prompts | 0 | 2/sem | Refinamiento permanente con casos reales |

### Resumen del cronograma

- **Fase 0** ~ 1 semana de trabajo activo (RP-04; el resto ya está hecho).
- **Fase A** ~ 2 semanas (en paralelo).
- **Fase B** ~ 8-10 semanas (en serie por categoría).
- **Fase C** ~ 1 semana de PR + calibración continua.
- **Total estimado: ~12-14 semanas** desde el inicio del Sprint 0 hasta PR mergeado, sin contar calibración continua.

### Dependencias externas críticas (a resaltar en el Master Track)

| Dependencia | Bloquea |
|---|---|
| Zendesk API (RP-A1) | RP-B1, RP-B2, RP-B4 |
| OpenAI key (RP-A2) ✅ disponible | RP-B1, RP-B2 (estado), RP-B3, RP-B4 |
| GitHub API + URLs repos (RP-A3) | RP-B3 |
| Slack API/MCP (RP-A4) | RP-B4 (parcial — 3 sub-reglas Feedbacks) |
| Stats API (RP-A5) — incierto | RP-B2 (parcial — 1 sub-regla Id Org) |
| Lake discovery (RP-A6) | RP-B2, RP-B3 |

### Hitos visibles (milestones)

🎯 **Milestone 1**: Evaluador "por guru × proceso" funcional — fin Fase 0
🎯 **Milestone 2**: Todas las fuentes externas conectadas — fin Fase A
🎯 **Milestone 3**: Clasificación 100% automatizada — fin RP-B1
🎯 **Milestone 4**: Procesos Zendesk 95% automatizado (sin Stats si no sale) — fin RP-B2
🎯 **Milestone 5**: I&P + KB cerrados — fin RP-B4
🎯 **Milestone 6**: PR mergeado a robia-qa — fin RP-C1
🎯 **Milestone 7**: Primera muestra completa evaluada por RobIA Procesos en producción — Fase C en marcha

---

## Anexos

### Tickets de referencia usados en discovery
- 7189367 (calibración manual completa, multitópico, multinaturaleza, hold con SD)
- 7253209, 7270214, 7242316, 7243898 (smoke tests)

### Repos relacionados
- `SofGrande/robia-process` — desarrollo actual (privado)
- `TiendaNube/robia-qa` — repo padre, PR objetivo final

### Documentos de referencia (sin tocar)
- IQS Guideline (Diciembre 2024) — `[AR_LATAM] Support - IQS Guideline.txt` en repo
- Guideline operativa robia-qa: `https://docs.google.com/document/d/1xE7YCwWvBN6zHN9aQey67aekJrsuy1qamCq7w209Ls8/`
- Prompts oficiales Soft Skills: `https://docs.google.com/document/d/1x0njSmC9NfxmLfTYFxyu5xIZrnW6OBPGcKeqBTQrB4Q/`

### Definiciones canónicas (uso en prompts LLM)
Todas las definiciones de Sección 1.2 son **literales de Sofía**. Se usan textuales en system prompts para mantener coherencia entre auditoría humana y automática.
