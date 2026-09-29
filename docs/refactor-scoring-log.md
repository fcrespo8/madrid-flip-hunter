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

---

## Prompt caching

**Qué hice**
- `backend/scoring/client.py`:
  - Constantes `CACHE_CONTROL`, `CACHED_TOOLS`, `CACHED_SYSTEM` y `TOOL_CHOICE`, que se arman **una sola vez al importar**.
  - `SCORE_TOOL` lleva `cache_control: {"type": "ephemeral"}` en la copia que se manda a la API; el original en `prompt.py` no se toca. El `SYSTEM_PROMPT` pasa a ser un bloque de texto con `cache_control`.
  - Nueva función `build_request_params(user_message)`, que arma la request completa. El contexto del piso va solo en `messages`, sin `cache_control`.
  - `ScoreResponse` ahora guarda `cache_creation_input_tokens` y `cache_read_input_tokens`, y tiene `usage_details()` para Langfuse. Descarta los valores `None`, porque Langfuse espera `Dict[str, int]`.
- `backend/scoring/graph.py`: la generation `llm_score` manda `usage_details=response.usage_details()`, que incluye `input`, `output`, `cache_creation_input_tokens` y `cache_read_input_tokens`.
- `tests/test_scoring.py`: 5 tests nuevos (50 passed, 1 skipped). `FakeMessages` ahora acepta un `usage` configurable.

**Por qué**
- Cada llamada de scoring repite el mismo system prompt y la misma tool; lo único que cambia es el piso. Con caché, desde la segunda llamada ese prefijo se cobra al 10 % del precio de input.

**Lo que verifiqué en la doc oficial** (`platform.claude.com/docs/en/build-with-claude/prompt-caching`)
- Mínimo cacheable para **Claude Sonnet 4.6: 1.024 tokens**. Si el prefijo tiene menos, simplemente no se cachea: no da error y `cache_creation_input_tokens` vuelve en 0.
- El prefijo se arma en el orden `tools → system → messages`. `cache_control` en la última tool cachea todas las tools.
- Se permiten hasta 4 breakpoints (usamos 2). El TTL por defecto es de 5 minutos.
- En `usage`, `input_tokens` cuenta **solo** lo que no se leyó ni se escribió en caché, o sea, lo que va después del último breakpoint.
- Aparte, la doc de tool use (`agents-and-tools/tool-use/overview`) dice que con `tool_choice` de tipo `tool` la API agrega **589 tokens** de system prompt propio en Sonnet 4.6.

**¿El prefijo supera el mínimo? — Casi seguro que sí, pero no lo pude confirmar al 100 % offline**

Lo medí sin llamar a la API:

| Parte | Caracteres | Palabras + signos |
|---|---|---|
| `SYSTEM_PROMPT` | 2.577 | 622 |
| `SCORE_TOOL` (JSON) | 661 | 217 |
| **Total nuestro** | **3.238** | **839** |

- El tokenizer de Claude no está disponible offline, y el conteo de `tiktoken` no sirve para Claude. Tomando entre 3 y 3,5 caracteres por token, que es lo típico para español, **nuestro contenido da entre ~925 y ~1.080 tokens**. Queda justo alrededor del mínimo de 1.024.
- Si además cuentan los 589 tokens que agrega la API por la tool, el prefijo queda en **~1.500–1.700 tokens**, cómodo por encima. La doc no aclara si esos tokens cuentan para el mínimo, así que no lo doy por hecho.
- **Cómo confirmarlo** (elegí la opción conservadora y no lo corrí, porque pediste no llamar a la API):
  - Opción A, que no genera texto: una llamada a `client.messages.count_tokens(...)` con `build_request_params(...)` sin `max_tokens` devuelve el total exacto.
  - Opción B: en la primera corrida real, mirar en Langfuse que `cache_creation_input_tokens > 0` en la primera generation y que `cache_read_input_tokens > 0` en las siguientes.
  - Si da 0 en las dos, el prefijo no llega al mínimo. No se rompe nada, solo no hay ahorro. En ese caso se podría ampliar el prompt, por ejemplo con más criterios, o dejarlo así.

**El prefijo es idéntico entre llamadas**
- `SYSTEM_PROMPT` y `SCORE_TOOL` son literales fijos, sin fechas, IDs ni nada del listing.
- `MODEL_ID` se lee de la variable de entorno una sola vez, al importar. `TOOL_CHOICE` es constante.
- Los dicts de Python mantienen el orden de inserción, así que la serialización JSON es estable.
- Un test lo cubre (`test_prefijo_cacheado_identico_entre_listings`): arma la request para dos pisos distintos y compara byte a byte `model`, `tools`, `system` y `tool_choice`. También verifica que nada del piso aparezca en el prefijo.
- Única salvedad: el texto del prompt dice "Idealista abr 2026", pero eso está en el contexto del piso (en `messages`), no en el prefijo cacheado.

**Qué mirar en el diff**
- `client.py`: las 4 constantes nuevas y `build_request_params`.
- `test_request_lleva_cache_control_en_tool_y_system`: verifica que `cache_control` esté en la tool y en el system, y que **no** esté en `messages`.

**Dudas / decisiones**
1. **Dos breakpoints (tool y system), como pediste.** El de `system` ya cubre `tools + system` porque el prefijo es acumulativo. El de la tool sola queda por debajo del mínimo y en la práctica no crea una entrada propia. No hace daño y deja explícita la intención. Si querés simplificar, alcanza con el del `system`.
2. **TTL de 5 minutos, el default.** El scoring corre en serie dentro de `run_all`, así que dentro de un lote la caché queda caliente. No usé `ttl: "1h"`: la escritura cuesta más (2x en vez de 1,25x) y el scheduler corre una vez por día, así que no hay nada que reutilizar entre corridas.
3. **Costo cuando hay un solo candidato:** si en una corrida Claude puntúa **un solo** piso, ese prefijo se cobra a 1,25x (escritura sin lectura posterior). El punto de equilibrio son 2 llamadas dentro de 5 minutos. Con Sonnet 4.6 a 3 US$/M de input y un prefijo de ~1.600 tokens, el ahorro es de ~0,004 US$ por llamada cacheada, y el sobrecosto en el peor caso es de ~0,001 US$ por corrida. En los dos casos el monto es chico.
4. **Nombres en Langfuse:** usé `cache_creation_input_tokens` y `cache_read_input_tokens`, los mismos que devuelve Anthropic, como pediste. `usage_details` en Langfuse acepta claves libres (`Dict[str, int]`). **No verifiqué** que el cálculo de costos de Langfuse Cloud reconozca esas claves para aplicar el precio reducido. Los números aparecen igual en la traza; si el costo mostrado sale mal, hay que ajustar la definición del modelo en Langfuse.
5. Confirmé en el SDK instalado (`anthropic` 0.91.0) que `ToolParam` y `TextBlockParam` aceptan `cache_control`.

---

## Tests aislados de la DB real

**Qué cambió:** ya **no hace falta pasar `DATABASE_URL` a mano** para correr los tests. Alcanza con:

```bash
poetry run pytest tests/ -q
```

Esto reemplaza el comando largo del principio de este log (con `DATABASE_URL=...` y demás). El comando largo sigue funcionando, pero ya no hace falta.

**Qué hice**
- `tests/conftest.py` (nuevo). pytest lo carga antes que cualquier test y:
  - siempre **reemplaza** `DATABASE_URL`, así que nunca se usa la del `.env` ni la del shell. `load_dotenv()` no pisa variables que ya existen, por eso no puede volver a traer la real;
  - si definís `TEST_DATABASE_URL`, los tests de DB corren contra esa base; si no, se saltean;
  - aborta antes de correr nada si:
    - el nombre de la base de `TEST_DATABASE_URL` no contiene "test";
    - es la misma base que la del `.env`. Compara host, puerto y nombre ya normalizados con `make_url`: `localhost`, `127.0.0.1`, `::1` y el socket local cuentan como iguales, el puerto por defecto (5432) se completa, y no se tienen en cuenta usuario, password ni driver;
    - el engine de SQLAlchemy terminó apuntando a otra base;
    - se está en CI (`CI` definida) y falta `TEST_DATABASE_URL`.
- `tests/test_operations_model.py`: saqué el `load_dotenv()` y la heurística `_has_real_db()`, y ahora usa `@requires_test_db` del conftest. **Este era el test que podía escribir en la base real.**
- `backend/scoring/`: `temperature=0` en la llamada a Claude (constante `TEMPERATURE` en `prompt.py`).
- `.github/workflows/ci.yml`:
  - agregué un servicio Postgres `pgvector/pgvector:pg18` con la base `flip_test`, y definí `TEST_DATABASE_URL` a nivel del job;
  - agregué el paso `alembic upgrade head` antes de los tests;
  - saqué el `DATABASE_URL` ficticio.
  - Con esto, el test de DB corre en CI y no se saltea.

**Cómo correr el test de DB localmente (opcional)**

Necesitás un Postgres con pgvector y una base cuyo nombre contenga "test". Por ejemplo, con Docker:

```bash
docker run -d --name flip-test-db -p 5433:5432 \
  -e POSTGRES_USER=flipuser -e POSTGRES_PASSWORD=flippass -e POSTGRES_DB=flip_test \
  pgvector/pgvector:pg18
DATABASE_URL=postgresql://flipuser:flippass@localhost:5433/flip_test poetry run alembic upgrade head
TEST_DATABASE_URL=postgresql://flipuser:flippass@localhost:5433/flip_test poetry run pytest tests/ -v
```

Uso el puerto 5433 para no chocar con el Postgres del `docker-compose`.

**Qué verifiqué**
- Sin variables: 50 passed y 1 skipped (el test de DB, con un motivo claro).
- Con `DATABASE_URL` exportada apuntando a una base "de prod": se reemplaza y todo corre igual.
- Con un `TEST_DATABASE_URL` sin "test" en el nombre, y simulando CI sin `TEST_DATABASE_URL`: pytest aborta con exit code 4 y no corre ningún test.
- La normalización de URLs, con casos que deben dar igual y casos que deben dar distinto.
- El YAML del workflow parsea bien, y `alembic heads` devuelve una sola cabeza (`f3a1b2c4d5e6`).

**Lo que NO pude verificar**
- **El workflow nuevo no lo corrí.** Acá el daemon de Docker no está levantado, y el Postgres de Homebrew no tiene pgvector. La primera ejecución real va a ser en GitHub Actions. Lo más probable que falle, si algo falla, es la migración desde cero (`alembic upgrade head` sobre una base vacía), porque nunca se probó en CI.
- El check de "misma base que el `.env`" no lo probé con tu `.env` real. Es una comparación directa de URLs normalizadas.

**Dudas / decisiones**
1. ~~Elegí `pg14` para que coincida con `docker-compose.yml`.~~ **Actualizado:** producción usa PostgreSQL 18, así que CI y `docker-compose.yml` pasaron a `pgvector/pgvector:pg18` (ver la sección siguiente).
2. Agregué la regla de que en CI falte `TEST_DATABASE_URL` aborta: no la pediste, pero es lo que garantiza que el test de DB no se saltee en silencio si alguien después borra la variable.
3. `pytest.exit(..., returncode=2)` durante la carga del conftest termina con código 4 (error de uso de pytest). Igual es distinto de 0, así que CI falla, que es lo que importa.

---

## Postgres 18 en CI y docker-compose

**Qué hice**
- `.github/workflows/ci.yml`: el servicio pasó de `pgvector/pgvector:pg14` a `pgvector/pgvector:pg18`, igual que producción.
- `docker-compose.yml`: el servicio `db` pasó de `postgres:14` a `pgvector/pgvector:pg18`. Además de alinear la versión, esto arregla algo que ya estaba roto: `postgres:14` no trae pgvector, así que `alembic upgrade head` fallaba contra la base de compose en la migración que hace `CREATE EXTENSION vector`.

**Impacto en los datos locales de docker-compose**
- **Los datos de un Postgres 14 no se pueden abrir con Postgres 18.** Cambiar de versión mayor exige hacer dump y restore (o `pg_upgrade`).
- El `db` de compose **no tiene un volumen con nombre**: los datos viven en el volumen anónimo que crea la imagen. Al cambiar de imagen, según cómo Docker reasigne ese volumen, pueden pasar dos cosas:
  - **Postgres 18 arranca con una base vacía.** La imagen 18 guarda los datos en `/var/lib/postgresql`, no en `/var/lib/postgresql/data`, así que no ve los datos viejos.
  - **No arranca**, porque detecta datos viejos o incompatibles.
- En ninguno de los dos casos se borran los datos viejos: quedan en el volumen anónimo anterior, sin usar. Pero tampoco se migran solos.
- **No pude revisar si tenés datos locales**: el daemon de Docker no está corriendo.
- Hay un problema que ya existía: sin un volumen con nombre, un `docker compose down` seguido de `up` crea un volumen anónimo nuevo, así que los datos locales ya se "perdían" en cada recreación. No lo cambié porque no me lo pediste.
