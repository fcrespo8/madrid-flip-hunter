# Refactor del scoring — log

Objetivo: juntar `backend/agents/scoring_agent.py` (viejo) y `backend/pipeline/scoring_graph.py` (grafo) en un solo módulo `backend/scoring/`.

Reglas que seguí: sin commits ni push, sin llamar a la API real de Claude, sin correr `reset_and_rescore --confirm`. Todos los tests son offline, con cliente, DB y Langfuse falsos.

**Cómo corrí los tests (en todas las etapas):**

```bash
DATABASE_URL=postgresql://test@localhost/test ANTHROPIC_API_KEY=test-key \
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= poetry run pytest tests/ -q
```

Paso las variables a mano, igual que en CI. `tests/test_operations_model.py` hace `load_dotenv()`: si no las paso, usa el `DATABASE_URL` del `.env` y ese test escribe en la base real. Las claves de Langfuse vacías evitan que se manden trazas.

Punto de partida: 9 passed, 1 skipped.

---

## Etapa 1 — Crear `backend/scoring/` sin tocar el código viejo

**Qué hice**

Archivos nuevos:
- `backend/scoring/__init__.py`
- `backend/scoring/prompt.py`: `SYSTEM_PROMPT`, `SCORE_TOOL`, `MODEL_ID` y `MAX_TOKENS`. También `build_listing_context(listing, market_price)`, una función pura, y `build_user_message(listing, rag_context)`.
- `backend/scoring/client.py`: el cliente Anthropic singleton (`get_client()`), `request_score()` con `tool_choice` forzado a `score_listing` y `validate_score_result()`. Errores tipados: `ScoringError` → `LLMCallError`, `NoToolUseError`, `ScoreValidationError`.
- `backend/scoring/rag.py`: `get_rag_context(db, listing, retriever=None)`. Si la consulta falla, hace `db.rollback()` y devuelve contexto vacío.
- `backend/scoring/graph.py`: `build_scoring_graph(client=None, retriever=None)`, con el flujo `retrieve_rag → score → validate → save`. No tiene nodo notify. Si hay error en `score` o en `validate`, el grafo termina sin guardar.
- `backend/scoring/runner.py`: `run_scoring(listings=None, db=None, *, client=None, retriever=None, limit=None)`. Devuelve un `ScoringSummary` (`total`, `scored_ids`, `failed`) y tiene `__main__`.
- `tests/test_scoring.py`: 31 tests offline.

**Por qué**
- Antes había dos copias del mismo flujo, y cada fix había que hacerlo dos veces (pasó con el commit `397740f`).
- El cliente, el RAG y el retriever se pueden inyectar, así los tests no necesitan red, base de datos ni el modelo de embeddings.

**Qué mirar en el diff**
- `prompt.py`: el prompt y el tool schema son idénticos a los viejos. Lo verifiqué con un script aparte (no quedó como test): comparé byte a byte el mensaje de usuario del grafo viejo contra `build_user_message` en 3 casos (con m², sin m² ni barrio, con m²=0), y también `SYSTEM_PROMPT` y `SCORE_TOOL`. Todo dio igual.
- `client.py → validate_score_result`: ahora es **estricta**. Exige los 4 campos, `score` numérico entre 0 y 10 (un booleano no cuenta como número), `reasoning` no vacío y listas de strings en los flags. Antes, si faltaban los flags, se guardaba igual con `""`.
- `graph.py`: el ruteo tiene dos aristas condicionales (`score → validate|END` y `validate → save|END`). `save` devuelve `saved=True/False`.
- `runner.py`: la línea `if listings is not None and db is None: raise ValueError`.

**Tests:** 40 passed, 1 skipped. `ruff check backend/`: sin errores.

**Dudas / decisiones**
1. **Variable del modelo**: la llamé `SCORING_MODEL_ID` (default `claude-sonnet-4-6`). Se lee al importar el módulo.
2. **Validación estricta de flags**: la pediste así. El riesgo es chico, porque con `tool_choice` forzado y `required` en el schema Claude casi siempre los manda. Si faltan, ese listing no se guarda y aparece en `failed`.
3. **RAG con rollback sobre la sesión compartida** (y no con una sesión aislada como hacía el viejo): es lo que pediste. Es seguro porque `run_all` hace commit antes del scoring y cada score hace commit propio, así que el rollback no pisa nada pendiente.
4. **Pendientes = solo activos**: sin `listings`, el runner busca `score IS NULL AND is_active`, ordenado por id. El viejo también puntuaba inactivos, lo que gastaba API en listings que no se muestran.
5. **`listings` sin `db` → `ValueError`**: el viejo abría una sesión nueva para listings de otra sesión, y por eso su rollback no hacía nada. Prefiero fallar explícito.
6. **`scored_ids` en vez de objetos**: si el runner cierra su propia sesión, los objetos ORM quedan desconectados y leerlos puede fallar.
7. **Langfuse**: mismos nombres que en el grafo actual (`score_listing` como span raíz, `retrieve_rag` como retriever, `llm_score` como generation) y `pipeline: "langgraph"`. Si la validación falla, el error va a la metadata del span raíz. Hay un solo `flush` al final del lote. No agregué un span `validate` para no cambiar la forma de las trazas.
8. **`listing.price = None`** sigue rompiendo el armado del prompt, igual que antes: no quise cambiar el texto del prompt. En la práctica el QA filtra esos casos. Con la red de seguridad del runner, ese listing va a `failed` y el lote sigue.
9. Los reintentos siguen siendo los del SDK (2 por defecto). No agregué reintentos propios.
10. El cliente se crea en el primer uso y no al importar el módulo, así los tests no lo necesitan.

---

## Etapa 2 — Conectar lo nuevo

**Qué hice**
- `backend/scrapers/run_scrapers.py`:
  - `run_all` ahora llama a `run_scoring(candidatos_claude, db)`.
  - Saqué el `try/except ImportError` y el camino viejo.
  - Agregué `notify_scored()` y la constante `NOTIFY_MIN_SCORE = 7.5`.
- `backend/agents/reset_and_rescore.py`, reescrito:
  - Por defecto hace **dry-run**: muestra cuántos listings activos se resetearían y puntuarían, con una vista previa de hasta 10, y no escribe en la DB ni llama a Claude.
  - Solo ejecuta con `--confirm`.
  - Con `--limit N` se queda con los primeros N activos, ordenados por id.
  - El reset ahora también pone `scored_at = NULL`.
  - Usa `run_scoring` e imprime cuántos se guardaron, cuántos fallaron y por qué.
- `tests/test_scoring.py`: 5 tests más, sobre el dry-run, `--confirm` con `--limit`, el fallo con `--confirm`, los argumentos y `notify_scored`.

**Por qué**
- El grafo nuevo no notifica (lo pediste así), pero `run_all` tiene que seguir mandando los WhatsApp. Ahora la notificación queda en `run_all`, que es quien decide.
- Un dry-run por defecto evita gastar API o borrar scores por accidente.

**Qué mirar en el diff**
- `notify_scored`: solo avisa a listings que **se puntuaron y guardaron en esta corrida** (están en `scored_ids`), con score ≥ 7.5 y sin `notified_at`. Es el mismo criterio que usaba el grafo actual. Si guardar falló, no se notifica, y eso arregla el bug del grafo viejo.
- `reset_and_rescore.main()`: la rama `if not confirm` sale antes de cualquier escritura.

**Tests:** 45 passed, 1 skipped. `ruff`: sin errores.

**Dudas / decisiones**
1. **`--limit N` resetea solo esos N**, no todos. Si reseteara todos y puntuara N, el resto quedaría con score NULL y desaparecería del dashboard, que filtra `score IS NOT NULL`.
2. **Notificación por corrida y no por consulta global**: el camino viejo de fallback buscaba *todos* los listings con score ≥ 7.5 sin notificar. Después de un `reset_and_rescore` (que no notifica), eso podía disparar una ráfaga de WhatsApp en el siguiente `run_all`. Me quedé con el criterio del grafo actual, que es el que corre en producción.
3. **`reset_and_rescore` no notifica**, igual que antes.
4. **Riesgo que ya existía**: con `--confirm`, si Claude falla para un listing, ese listing queda con score NULL y sale del dashboard hasta el próximo scoring. Lo dejé así porque pediste resetear. La salida del script lista los fallidos para poder reintentarlos.
5. **El dry-run igual lee la DB** (necesita `DATABASE_URL`) para contar. No escribe.
6. `send_whatsapp_alerts` sigue sin avisar si faltan credenciales de Twilio (solo loguea un warning), así que `notified_at` se marca igual. Es el mismo comportamiento que antes y no lo toqué.

---

## Etapa 3 — Limpiar

**Qué hice**
- Borré `backend/agents/scoring_agent.py` y `backend/pipeline/scoring_graph.py`, con `rm` y no con `git rm`, así no queda nada staged.
- `tests/test_smoke.py`: `SCORE_TOOL` ahora se importa desde `backend.scoring.prompt`.
- `CLAUDE.md`: reemplacé la sección "Scoring Agent" por "Scoring (`backend/scoring/`)".

**Por qué:** ya no los usaba nadie. Busqué con `git grep` y no queda ninguna referencia en el código.

**Qué mirar en el diff**
- `CLAUDE.md`: además de la sección de scoring, cambié **una línea** de la sección de comandos. Decía `python -m backend.agents.scoring_agent`, que ahora apunta a un archivo borrado. La reemplacé por `python -m backend.scoring.runner`.

**Tests:** 45 passed, 1 skipped. `ruff`: sin errores. También importé a mano `backend.api.main`, `run_scrapers`, `reset_and_rescore` y `scoring.runner`, y cargan sin errores.

**Dudas / decisiones**
1. Dejé `backend/pipeline/__init__.py`, que quedó como un paquete vacío. No me pediste borrarlo; se puede sacar después.
2. **No toqué `README.md` ni `CONTEXT.md`**, aunque todavía mencionan `scoring_graph.py`, `run_scoring_agent()` y el fallback por `ImportError` (por ejemplo, `README.md:34` y `:164`, `CONTEXT.md:13` y `:41`). Pediste actualizar solo `CLAUDE.md`. Quedan pendientes.
3. La línea "Pipeline:" de `CLAUDE.md` (la que dice "Wallapop API → … → Scoring Agent → …") está desactualizada desde antes (hoy hay 5 scrapers). No la cambié porque no es de la sección de scoring.

---

## Etapa 4 — Cierre

**Tests finales:** 45 passed, 1 skipped. El que se saltea es `test_operation_with_expense`, que necesita una DB real y se saltea a propósito. `ruff check backend/`: sin errores.

### Resumen general

Ahora el scoring vive en un solo lugar, `backend/scoring/`, con una sola API pública: `run_scoring()`. El prompt, el schema y la llamada a Claude ya no están duplicados. El resultado se valida antes de guardar, y un score inválido no se guarda. Si falla el RAG, la sesión ya no queda rota. Solo se notifica cuando el score se guardó. `reset_and_rescore` es seguro por defecto (dry-run) y acepta `--limit`. Hay 36 tests nuevos, todos offline.

El prompt que recibe Claude es **idéntico** al de antes; lo verifiqué byte a byte. Lo que cambia es qué pasa alrededor de la llamada: la validación estricta, que solo se busquen pendientes activos y cuándo se notifica.

### Archivos tocados

| Archivo | Cambio |
|---|---|
| `backend/scoring/__init__.py` | nuevo |
| `backend/scoring/prompt.py` | nuevo |
| `backend/scoring/client.py` | nuevo |
| `backend/scoring/rag.py` | nuevo |
| `backend/scoring/graph.py` | nuevo |
| `backend/scoring/runner.py` | nuevo |
| `tests/test_scoring.py` | nuevo (36 tests) |
| `docs/refactor-scoring-log.md` | nuevo (este archivo) |
| `backend/scrapers/run_scrapers.py` | modificado: usa `run_scoring` y agrega `notify_scored` |
| `backend/agents/reset_and_rescore.py` | reescrito: dry-run, `--confirm`, `--limit` y reset de `scored_at` |
| `tests/test_smoke.py` | modificado: nuevo import |
| `CLAUDE.md` | modificado: sección de scoring y una línea de comandos |
| `backend/agents/scoring_agent.py` | borrado |
| `backend/pipeline/scoring_graph.py` | borrado |

### Cómo probarlo a mano

1. **Tests y lint (offline):**
   ```bash
   DATABASE_URL=postgresql://test@localhost/test ANTHROPIC_API_KEY=test-key \
   LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= poetry run pytest tests/ -v
   poetry run ruff check backend/
   ```
   Tiene que dar 45 passed y 1 skipped.

2. **Mirar el diff:** `git status` y `git diff`. Los archivos nuevos no aparecen en `git diff` porque están sin trackear; abrilos directo en `backend/scoring/`.

3. **Dry-run de `reset_and_rescore`.** Usa tu DB del `.env`, solo lee y no llama a Claude:
   ```bash
   poetry run python -m backend.agents.reset_and_rescore
   poetry run python -m backend.agents.reset_and_rescore --limit 3
   poetry run python -m backend.agents.reset_and_rescore --help
   ```
   Tendrías que ver `[dry-run] Se resetearían y puntuarían N listings activos…`, una lista de hasta 10 y `Nada se modificó`. Para confirmar que no escribió nada, compará el `scored_at` de esos listings antes y después.

4. **Prueba real chica (cuesta API).** Usá solo 1 o 2 listings:
   ```bash
   poetry run python -m backend.agents.reset_and_rescore --confirm --limit 1
   ```
   Tendrías que ver `Rescore: 1 guardados, 0 fallidos.` y, en el dashboard, ese listing con score nuevo y `scored_at` de hoy. Si tenés Langfuse configurado, aparece una traza `score_listing` con `retrieve_rag` y `llm_score` adentro.

5. **Pendientes con el runner (cuesta API):** `poetry run python -m backend.scoring.runner` puntúa todos los activos con score NULL. Si no hay ninguno, loguea `0 listings para scoring` y termina.

6. **Pipeline completo (scrapea, cuesta API y puede mandar WhatsApp):** `poetry run python -m backend.scrapers.run_scrapers`. Conviene correrlo una vez antes de deployar, o dejar que lo corra el scheduler.

### Pendientes sugeridos (no hechos)
- Actualizar `README.md` y `CONTEXT.md`.
- Borrar `backend/pipeline/__init__.py` si no se va a usar.
- Hacer que `send_whatsapp_alerts` indique si realmente envió, para no marcar `notified_at` cuando faltan credenciales de Twilio.
- Manejar `listing.price = None` en el prompt, si alguna vez el QA deja pasar uno.
