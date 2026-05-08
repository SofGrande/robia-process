# Guía: columnas de la matriz de discovery (RobIA Procesos)

Documento de apoyo para completar `RobIA_Procesos_discovery_matriz.csv` en Google Sheets. Incluye **Español** y **Português (Brasil)**.

---

## ES — Cómo completar cada columna

**Objetivo de la matriz**  
Cada fila vincula un **proceso o criterio de la IQS** con **evidencia en Databricks** (tablas, campos, eventos), de modo que el equipo sepa qué se puede medir con datos y qué no.

1. **Proceso (IQS)**  
   Categoría de la guideline: por ejemplo *Clasificación de la conversación*, *Procesos Zendesk*, *Issues & Problems*, *Knowledge Base*.  
   *Cómo completarla:* usá el mismo nombre que en la IQS para que el discovery sea trazable a la evaluación humana.

2. **Subcriterio / paso**  
   Desglose dentro del proceso: p. ej. *Tópico y subtópico*, *Naturaleza de la conversa*, *Duplicates*, *Estado de la conversación*, *Side conversation*, *Reporte de issues*, *Uso de materiales*, *Feedback*, etc.  
   *Cómo completarla:* una sub-fila por cada señal que quieran mapear por separado (no mezclar dos procesos en una sola fila).

3. **Señal observable (qué audita)**  
   En lenguaje de **auditoría**: qué miraría un revisor en Zendesk o en el ticket para decidir si se cumplió el proceso.  
   *Cómo completarla:* frases concretas (ej. “fusionar duplicados cuando aplica”, “estado coherente con jornada Guru”, “completar multitópico”). Evitá términos solo técnicos sin el “qué se evalúa”.

4. **Tabla Databricks**  
   Nombre de la **tabla o vista** (con *schema* si aplica: `schema.tabla`) donde vive el dato.  
   *Cómo completarla:* poner el identificador exacto del catálogo. Si aún no lo saben, dejar **(completar)** o el nombre tentativo y actualizar cuando exploren el *lake*.

5. **Campo o tipo de evento**  
   Columna(s) relevante(s) o el **tipo de evento** en tablas de auditoría (p. ej. `custom_field_`, `status`, `via`, tipo `TicketMerged`).  
   *Cómo completarla:* listar campos o el nombre del evento. Si hace falta lógica entre varias columnas, aclararlo breve en *Notas y riesgos*.

6. **Grano**  
   **Nivel al que corresponde una fila** de datos: ticket, comentario, evento de cambio, side conversation, etc. (responde: “¿una fila = un ticket, un mensaje, un evento?”).  
   *Cómo completarla:* escribir en lenguaje claro, p. ej. *1 fila por ticket*, *1 fila por cambio de campo*, *1 fila por comentario público*. Esto evita confundir métricas calculadas con el dato bruto.

7. **Suficiencia**  
   Juicio preliminar: si con ese dato alcanza para **evaluar** o **entrenar** el proceso de forma fiable. Valores sugeridos: *Suficiente*, *Parcial*, *Falta dato* (o dejar *por definir* al inicio).  
   *Cómo completarla:* actualizar después de validar con muestras reales o con el owner de datos. *Parcial* = sirve con supuestos, enriquecimiento o reglas adicionales.

8. **Otras fuentes**  
   Sistemas además de Zendesk/Databricks: p. ej. *GitHub* (issues), *Slack* (#canal de feedback), *hojas de tópicos*, *procedimiento en vídeo*, etc.  
   *Cómo completarla:* si el proceso no vive 100% en el lake, indicar dónde está el resto. Si no aplica, dejar vacío o *N/A*.

9. **Notas y riesgos**  
   Macros que alteran campos, **multitópico**, retraso de ETL, campos no sincronizados, ambigüedad del evento, límites de la API, etc.  
   *Cómo completarla:* todo lo que pueda invalidar reglas automáticas o explicar por qué *Suficiencia* es *Parcial*.

10. **Fecha verificación**  
    Última vez que alguien **confirmó** tablas/campos o revisó la fila contra Databricks.  
    *Cómo completarla:* formato AAAA-MM-DD o el que use el equipo.

11. **Responsable**  
    Persona o squad que mantiene actualizada esa fila.  
    *Cómo completarla:* nombre, sigla o correo, según la convención del equipo.

**Buenas prácticas**

- Revisar filas con **auditoría humana** (muestra de tickets) cuando marquen *Suficiente* o *Parcial*.  
- Una fila = un **proceso/sub-señal**; si un mismo proceso requiere dos tablas distintas, usar dos filas.  
- Mantener nombres de IQS y de tablas alineados con la **guideline** y con el **catálogo** de Databricks.

---

## PT-BR — Como preencher cada coluna

**Objetivo da matriz**  
Cada linha liga um **processo ou critério da IQS** a **evidência no Databricks** (tabelas, campos, eventos), para o time saber o que dá para medir com dados e o que não dá.

1. **Proceso (IQS)**  
   Categoria da guideline: por exemplo *Clasificación de la conversación* / *Clasificação da conversa*, *Procesos Zendesk*, *Issues & Problems*, *Knowledge Base*.  
   *Como preencher:* use o mesmo nome que na IQS para rastreio com a avaliação humana.

2. **Subcriterio / passo**  
   Detalhe do processo: p. ex. *Tópico e subtópico*, *Natureza da conversa*, *Duplicates*, *Estado da conversa*, *Side conversation*, *Reporte de issues*, *Uso de materiais*, *Feedback*, etc.  
   *Como preencher:* uma sub-linha para cada sinal mapeado separadamente (não misturar dois processos na mesma linha).

3. **Señal observable (qué audita)** (sinal observável)  
   Em linguagem de **auditoria**: o que um revisor veria no Zendesk ou no ticket para decidir se o processo foi cumprido.  
   *Como preencher:* frases objetivas (ex.: “mesclar duplicados quando aplicável”, “status alinhado à jornada Guru”, “preencher multitópico”). Evite só jargão técnico sem o “o que avaliamos”.

4. **Tabla Databricks**  
   Nome da **tabela ou view** (com *schema* se fizer sentido: `schema.tabela`) onde está o dado.  
   *Como preencher:* o identificador exato do catálogo. Se ainda desconhecer, use **(completar)** ou nome provisório e atualize após exploração do *lake*.

5. **Campo o tipo de evento**  
   Coluna(s) relevante(s) ou o **tipo de evento** em tabelas de auditoria (p. ex. `custom_field_`, `status`, `via`, tipo `TicketMerged`).  
   *Como preencher:* liste os campos ou o nome do evento. Se precisar de regra com vários campos, anote de forma curta em *Notas e riesgos*.

6. **Grano** (granularidade)  
   **Nível a que corresponde uma linha** de dado: ticket, comentário, evento de alteração, side conversation, etc. (responde: “uma linha = um ticket, uma mensagem, um evento?”).  
   *Como preencher:* texto claro, p. ex. *1 linha por ticket*, *1 linha por mudança de campo*, *1 linha por comentário público*. Isso evita confundir métricas com o dado bruto.

7. **Suficiencia**  
   Veredito preliminar: com esse dado dá para **avaliar** ou **treinar** o processo de forma confiável. Valores sugeridos: *Suficiente* (ou *Suficiente*), *Parcial*, *Falta dado* (ou *a definir* no início).  
   *Como preencher:* atualize depois de validar com amostra real ou com o owner de dados. *Parcial* = atende com suposições, enriquecimento ou regras extras.

8. **Otras fontes**  
   Sistemas além do Zendesk/Databricks: p. ex. *GitHub* (issues), *Slack* (canal de feedback), *planilhas de tópicos*, *procedimento em vídeo*, etc.  
   *Como preencher:* se o processo não vive 100% no *lake*, indique onde está o restante. Se não se aplica, deixe vazio ou *N/A*.

9. **Notas y riesgos** (notas e riscos)  
   Macros que alteram campos, **multitópico**, atraso de ETL, campos não sincronizados, ambiguidade do evento, limites da API, etc.  
   *Como preencher:* tudo que possa anular regras automáticas ou explicar *Parcial* em *Suficiencia*.

10. **Fecha verificación** (data de verificação)  
    Última vez em que alguém **confirmou** tabelas/campos ou rever a linha contra o Databricks.  
    *Como preencher:* AAAA-MM-DD ou o padrão do time.

11. **Responsable** (responsável)  
    Pessoa ou *squad* que mantém a linha.  
    *Como preencher:* nome, sigla ou e-mail, conforme a convenção.

**Boas práticas**

- Validar linhas com **auditoria humana** (amostra de tickets) ao marcar *Suficiencia* como adequada.  
- Uma linha = **um processo / sub-sinal**; se precisar de duas tabelas, use duas linhas.  
- Alinhar nomes da IQS e do catálogo Databricks com a **guideline** e o time de dados.

---

*Versão alinhada às colunas de `RobIA_Procesos_discovery_matriz.csv`.*
