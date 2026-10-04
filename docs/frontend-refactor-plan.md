# Plan: dividir `frontend/index.html` sin cambiar el comportamiento

Estado: **propuesta, sin implementar**. Los números de línea corresponden a `frontend/index.html` al 2026-10-04 y se van a correr a medida que se extraigan bloques; en cada paso se buscan por el comentario de sección (`// ── Nombre ──`), no por número.

## 1. Qué hay hoy

| Parte | Líneas | Tamaño |
|---|---|---|
| `<head>`: Leaflet CSS (unpkg) | 1–6 | — |
| `<style>` inline | 7–598 | ~590 líneas, 30 secciones `/* ── … ── */` |
| Markup (`<body>`) | 600–1348 | ~750 líneas (login, tabs Finder / Ops / Personas / Inversores / Calculadora, modales) |
| Leaflet JS (unpkg) | 1349 | — |
| `<script>` inline (uno solo) | 1350–3267 | ~1.915 líneas, 24 secciones `// ── … ──` |

Secciones del JS, en orden:

| # | Sección | Líneas | Destino propuesto |
|---|---|---|---|
| 1 | Toast notifications | 1351–1360 | `js/core.js` |
| 2 | Button loading state | 1361–1374 | `js/core.js` |
| 3 | Loading skeleton | 1375–1383 | `js/core.js` |
| 4 | Auth | 1384–1452 | `js/auth.js` |
| 5 | Modal helpers | 1453–1462 | `js/modal.js` (fase B: `core.js`) |
| 6 | SPA Router | 1463–1514 | `js/router.js` |
| 7 | Ops state + Ops list | 1515–1603 | `js/operations/list.js` |
| 8 | Operation detail page | 1604–1636 | `js/operations/detail.js` |
| 9 | Ficha tab | 1637–1790 | `js/operations/ficha.js` |
| 10 | Financiero tab | 1791–2042 | `js/operations/financiero.js` |
| 11 | Gastos tab | 2043–2161 | `js/operations/gastos.js` |
| 12 | Status update + Dates editing + CSV export + New operation modal | 2162–2303 | `js/operations/actions.js` (fase B: se reparte) |
| 13 | Utils (`escHtml`) | 2304–2306 | `js/utils.js` (fase B: `core.js`) |
| 14 | Sociedad (operation partners) | 2307–2461 | `js/operations/sociedad.js` |
| 15 | Personas global tab | 2462–2549 | `js/personas.js` |
| 16 | Inversores tab | 2550–2632 | `js/inversores.js` |
| 17 | Deal Finder (incluye el mapa) | 2633–2836 | `js/deal-finder.js` (opcional: `map.js` + `finder.js`) |
| 18 | Calculadora | 2837–3087 | `js/calculadora.js` |
| 19 | Compare mode | 3088–3239 | `js/compare.js` |
| 20 | Init (`loadListings` + arranque) | 3240–3266 | `js/main.js` |

**Cosas que condicionan el plan**
- **No hay build step** y FastAPI sirve `frontend/` como estáticos en `/` (`StaticFiles(directory=frontend, html=True)` en `backend/api/main.py`). Cualquier archivo nuevo en `frontend/css/` o `frontend/js/` se sirve solo. `.dockerignore` no los excluye.
- **63 handlers inline** (`onclick="…"`, `onchange="…"`) que llaman a **33 nombres globales**: `setOpStatus`, `filterOpsBy`, `switchTab`, `switchDetailTab`, `saveFinancials`, `doLogin`, `doLogout`, etc. Uno usa directamente la variable `let _calcSocios`. Si el código pasa a `type="module"`, esos nombres dejan de ser globales y **todos esos botones se rompen**.
- **El estado global es compartido entre secciones**: `currentOp`, `currentFinancialsData`, `_opsAll`, `allListings`, `markers`, `map`, `_calcSocios`, etc.
- **Constantes que usan varias secciones:**
  - `PIE_COLORS` se define en Sociedad y la usan Personas, Inversores y Calculadora.
  - `STATUS_LABELS` se define en Ops state y la usan list, detail y status update.
  - `SEMAFORO_TEXTS` se define en Calculadora y la usa Compare.

  En todos los casos la definición está antes de cualquier uso, así que el orden de la fase A ya funciona.
- **Código que corre al cargar.** Son listeners y llamadas top-level:
  - `addEventListener` en modales, login, router (`popstate`), Financiero, status dropdown y filtros del Finder;
  - `L.map('map')` en Deal Finder;
  - el bloque Init.

  Verifiqué que **todas las funciones que referencian al cargar están definidas en la misma sección o en una anterior**: `router`, `calcPnl`, `updateTaxLabel`, `applyFilters`, `initCalculadora`, `updateAuthUI`, `getToken`, `showLoginScreen`. `loadListings` se define recién en Init, pero solo se llama en tiempo de ejecución (desde `doLogin` y similares), nunca al cargar.
- **No hay tests de frontend.** Playwright ya está en las dependencias por los scrapers.

## 2. Principios

1. **Scripts clásicos, no módulos.** Cada archivo se carga con `<script src="js/….js"></script>`, sin `type="module"` ni `defer`, **en el mismo lugar** donde hoy está el `<script>` inline (al final del `<body>`, después de Leaflet).
   - Los scripts clásicos comparten el ámbito global: las funciones siguen en `window` y los `let`/`const` de nivel superior siguen visibles entre archivos, así que los handlers inline siguen funcionando.
   - Se cargan y ejecutan en orden, con el DOM ya parseado, igual que hoy.
2. **Fase A: solo mover, en orden.** Cada paso corta un bloque **contiguo** del principio del `<script>` inline y lo pega tal cual en un archivo, sin renombrar, reformatear ni "arreglar" nada. Como siempre se extrae desde arriba, el orden de ejecución queda idéntico.
3. **Fase B: reagrupar, solo declaraciones.** Recién cuando no queda JS inline se mueven bloques que **solo declaran funciones** a su archivo natural (por ejemplo, el CSV export a `gastos.js`). Mover declaraciones no cambia el comportamiento, siempre que nada las llame durante la carga antes de que existan; eso se verifica en cada paso.
4. **El markup queda en `index.html`.** Partirlo en plantillas exigiría `fetch` o un build, y eso sí cambia el comportamiento. Queda fuera de este plan.
5. **Un commit por paso.** Cada commit deja la app funcionando, así se puede revertir cualquiera por separado.

## 3. Verificación (se repite en cada paso)

Se arma en el paso 0 y se corre en todos los siguientes:

**V1. Chequeo mecánico** (`scripts/check_frontend_split.py <ref>`):
- Reconstruye un HTML único: reemplaza cada `<link rel="stylesheet" href="css/…">` y cada `<script src="js/…">` local por el contenido del archivo, en orden.
- Lo compara contra `git show <ref>:frontend/index.html`, ignorando solo los espacios en blanco al principio y al final de cada línea.
- En la **fase A** tiene que dar **idéntico**.
- En la **fase B** tiene que dar **el mismo multiconjunto de líneas** (mismas líneas, en otro orden). El script además imprime qué bloques cambiaron de lugar, para revisar a ojo que sean solo declaraciones.

**V2. Smoke test con Playwright** (`tests/frontend/test_smoke.py`, marcado `frontend`):
- Sirve `frontend/` con un servidor estático local y **mockea todo `/api/**`** con `page.route`, con fixtures JSON chicos. No toca el backend ni la DB.
- **Sin token:** se ve la pantalla de login y no hay `pageerror` ni `console.error`.
- **Con token** (`localStorage.mfh_token`) y 2 listings mockeados:
  - hay 2 filas en la tabla del Finder y 2 marcadores (`.leaflet-interactive`);
  - existe la capa `.map-tiles-dark`;
  - los KPIs no quedan en "—".
- **Navegación:** `switchTab(...)` por Finder, Ops, Personas, Inversores y Calculadora. Abrir una operación mockeada y recorrer sus tabs (Ficha, Financiero, Gastos, Sociedad). Abrir y cerrar el modal de nueva operación. En todo el recorrido, cero errores.
- **Los 33 nombres de los handlers inline existen:** `page.evaluate("typeof setOpStatus")` y así con cada uno tiene que dar `'function'`, y `typeof _calcSocios` tiene que dar `'object'`. La lista se obtiene parseando los `on*="…"` del HTML, así no queda desactualizada.

**V3. Revisión manual rápida** (2 minutos, con `uvicorn` local y una DB local, **no** la del `.env`, que es producción):
1. Login y logout.
2. En el Finder: filtros, ordenar, click en un marcador que resalte la fila y abra el detalle, y click en una fila que resalte el marcador.
3. En Operaciones: filtros por estado, crear una operación, cambiar el estado, editar fechas y exportar el CSV.
4. En el detalle de una operación: Ficha (editar y cancelar), Financiero (que el P&L recalcule al tipear), Gastos (alta y baja) y Sociedad.
5. Personas, Inversores, Calculadora (simple y comparar, guardar un escenario y recargar la página), y el layout responsive por debajo de 900px.

**Para que un paso cuente como terminado:** V1 OK, V2 en verde y V3 sin diferencias visibles.

## 4. Pasos

### Paso 0 — Red de seguridad (sin tocar `index.html`)
- Agregar `scripts/check_frontend_split.py` (V1) y `tests/frontend/test_smoke.py` (V2), con sus fixtures en `tests/frontend/fixtures/`.
- Registrar el marker `frontend` en pytest. En CI, agregar `poetry run playwright install --with-deps chromium` y correr esos tests en un paso aparte.
- **Verificar:** V2 en verde contra el `index.html` actual, y V1 contra `HEAD` (tiene que dar idéntico, porque todavía no se movió nada).
- **Commit:** `test(frontend): smoke test con Playwright y chequeo de split`

### Paso 1 — CSS a `frontend/css/app.css`
- Mover el contenido del `<style>` (líneas 8–597) a `css/app.css` y reemplazar el bloque por `<link rel="stylesheet" href="css/app.css">`, **después** del CSS de Leaflet, igual que hoy. Así se respeta el orden de la cascada.
- **Verificar:** V1, V2 y V3. En la revisión manual, prestar atención al responsive y a los tiles oscuros.
- **Commit:** `refactor(frontend): extraer CSS a css/app.css`

### Paso 2 — `js/core.js`: Toast, Button loading y Loading skeleton
- Cortar las secciones 1 a 3 desde el principio del `<script>` inline. Agregar `<script src="js/core.js"></script>` justo **antes** del `<script>` inline que queda.
- **Verificar:** V1, V2 y V3 (los toasts aparecen al guardar, y los botones muestran el estado de carga).
- **Commit:** `refactor(frontend): extraer js/core.js`

### Paso 3 — `js/auth.js`
- Mover la sección 4: `getToken`, `authHeaders`, `showLoginScreen`, `updateAuthUI`, `doLogin`, `doLoginScreen` y `doLogout`.
- **Verificar:** V1, V2 y V3 (login desde la pantalla y desde el modal, y logout).
- **Commit:** `refactor(frontend): extraer js/auth.js`

### Paso 4 — `js/modal.js` y `js/router.js`
- Dos commits, uno por sección (5 y 6). El de los modales incluye los listeners de overlay y de `Escape`, y el del router incluye `window.addEventListener('popstate', router)`.
- **Verificar:** V1 y V2. En V3: Escape cierra el modal, el click afuera también, y los botones atrás y adelante del navegador funcionan.
- **Commits:** `refactor(frontend): extraer js/modal.js` y `refactor(frontend): extraer js/router.js`

### Paso 5 — Operaciones, una sección por commit
En orden, siempre desde el principio del inline:
- 5a: `js/operations/list.js` (sección 7)
- 5b: `js/operations/detail.js` (sección 8)
- 5c: `js/operations/ficha.js` (sección 9)
- 5d: `js/operations/financiero.js` (sección 10). Incluye los `addEventListener('input', calcPnl)` top-level, que referencian funciones de la misma sección.
- 5e: `js/operations/gastos.js` (sección 11)
- 5f: `js/operations/actions.js` (sección 12). El listener global de click que cierra el dropdown de estado viaja acá.
- 5g: `js/utils.js` (sección 13)
- 5h: `js/operations/sociedad.js` (sección 14)
- **Verificar en cada uno:** V1 y V2, y en V3 la parte de Operaciones.
- **Commits:** `refactor(frontend): extraer js/operations/<archivo>.js`

### Paso 6 — `js/personas.js` y `js/inversores.js`
- Dos commits, para las secciones 15 y 16.
- **Verificar:** V1 y V2. En V3, los tabs de Personas e Inversores, incluido el gráfico de torta.

### Paso 7 — `js/deal-finder.js`
- Mover la sección 17 completa: `const map = L.map(...)`, el `tileLayer`, `markers`, `allListings`, filtros y detalle. Leaflet ya está cargado antes, como hoy.
- **Verificar:** V1 y V2 (marcadores y filas). En V3, todo el Finder.
- **Commit:** `refactor(frontend): extraer js/deal-finder.js`

### Paso 8 — `js/calculadora.js` y `js/compare.js`
- Dos commits, para las secciones 18 y 19. `_calcSocios` (usado en un handler inline) queda como `let` global en `calculadora.js`.
- **Verificar:** V1 y V2. En V3: modo simple, agregar y quitar socios, comparar, guardar un escenario y recargar la página (`localStorage` `flip_scenario_*`).

### Paso 9 — `js/main.js` y eliminar el `<script>` inline
- Mover Init (sección 20) a `js/main.js`. Tiene que ser **el último** `<script src>`. En ese punto `index.html` queda solo con markup y los `<link>`/`<script src>`.
- **Verificar:** V1 (tiene que dar idéntico a la versión con todo inline) y V2. V3 completo.
- **Commit:** `refactor(frontend): js/main.js; index.html sin JS inline`

> **Cierre de la fase A.** Desde acá, cualquier diferencia de comportamiento es un bug del paso que la introdujo: alcanza con revertir ese commit.

### Paso 10 (fase B) — Reagrupar declaraciones
Cada punto es un commit. En cada uno se usa V1 en modo multiconjunto, V2, y se confirma que el bloque que se mueve **no tiene sentencias top-level que se ejecuten al cargar**, salvo las que ya se sabe que son seguras.
- 10a: `escHtml` (`utils.js`) y los helpers de modal (`modal.js`) pasan a `core.js`. Se borran `utils.js` y `modal.js`. Los listeners de modal se mueven con ellos: necesitan que exista el DOM, y eso se cumple porque `core.js` sigue cargándose al final del `<body>`.
- 10b: el contenido de `operations/actions.js` se reparte:
  - Status update y Dates editing van a `detail.js`;
  - CSV export va a `gastos.js`;
  - New operation modal va a `list.js`;
  - después se borra `actions.js`. El listener de click del dropdown se va con Status update.
- 10c: `loadListings` (hoy en `main.js`) pasa a `deal-finder.js`.
- 10d: `PIE_COLORS` pasa de `operations/sociedad.js` a `core.js`. Se define en Sociedad, pero también la usan Personas, Inversores y Calculadora, así que es compartida.
  - Las demás constantes ya están con su único dueño: `STATUS_LABELS` en `operations/list.js` (la usan list, detail y status update, que cargan después), `CAT_LABELS` y `PAID_LABELS` en `gastos.js`, y `SEMAFORO_TEXTS` en `calculadora.js` (también la usa `compare.js`, que carga después). Esas no se mueven.
  - Antes de mover cualquiera, confirmarlo con `grep -nw NOMBRE frontend/js -r`.

### Paso 11 (opcional) — Dividir `deal-finder.js` en `map.js` y `finder.js`
- `map.js` tendría `map`, `tileLayer`, `scoreColor`, `makeMarker`, `highlightMarker` y `markers`. `finder.js` tendría filtros, tabla, detalle y KPIs.
- Hace falta leer las ~200 líneas: `map` y `markers` son estado compartido, y `map.js` tiene que cargarse **antes** que `finder.js`.
- **Verificar:** V1 (multiconjunto), V2 y el Finder en V3.

### Paso 12 (opcional) — Dividir el CSS por sección
- `css/base.css` (toast, header, botones, KPIs, tablas, modales, login, barra de carga), `css/finder.css`, `css/operations.css`, `css/society.css` (sociedad, personas, inversores), `css/calculadora.css` y `css/responsive.css`.
- **Riesgo: el orden de la cascada.** Los `<link>` tienen que quedar en el orden original de los bloques, y `responsive.css` va **último**, porque redefine `#map` y otros selectores en `@media (max-width: 900px)`. Si algún bloque no se puede asignar limpiamente, se queda en `base.css`.
- **Verificar:** V1 (idéntico si se respeta el orden) y V3 con foco visual, incluido el responsive.

### Paso 13 — Cache de los estáticos
- `StaticFiles` no versiona los archivos. Después de un deploy, un navegador podría combinar un `index.html` nuevo con un `app.js` cacheado de antes.
- Recomendación mínima: agregar `?v=N` a cada `<link>`/`<script src>` y subir `N` en cada deploy que toque el frontend. La alternativa es un header `Cache-Control: no-cache` para `/css` y `/js`, pero eso es un cambio de backend y va en otro PR.
- Conviene hacer este paso **junto con el paso 1**, o como mucho antes del primer deploy de la fase A.

## 5. Fuera de alcance (posible fase C)

- **Pasar a ES modules** (`type="module"`, `import`/`export`). Exige reemplazar los 63 handlers inline por `addEventListener`, o exponer explícitamente cada nombre en `window`. Además cambia el momento en que se ejecuta el código, porque los módulos se comportan como `defer`. Necesita su propio plan.
- Partir el markup en plantillas o componentes.
- Cualquier limpieza de lógica, renombre o cambio de estilo. Si aparece algo durante la fase A, se anota y se hace en un commit aparte, después.

## 6. Resumen de archivos al final de la fase B

```
frontend/
├── index.html                  (solo markup + <link>/<script src>)
├── css/app.css                 (o css/*.css si se hace el paso 12)
└── js/
    ├── core.js                 toast, botones, skeleton, modales, escHtml
    ├── auth.js
    ├── router.js
    ├── operations/
    │   ├── list.js             estado, lista, filtros, nueva operación
    │   ├── detail.js           página de detalle, estado, fechas
    │   ├── ficha.js
    │   ├── financiero.js
    │   ├── gastos.js           incluye CSV export
    │   └── sociedad.js
    ├── personas.js
    ├── inversores.js
    ├── deal-finder.js          (o map.js + finder.js si se hace el paso 11)
    ├── calculadora.js
    ├── compare.js
    └── main.js                 arranque (último <script>)
```

Orden de carga: Leaflet → `core` → `auth` → `router` → `operations/*` (list, detail, ficha, financiero, gastos, sociedad) → `personas` → `inversores` → `deal-finder` → `calculadora` → `compare` → `main`.
