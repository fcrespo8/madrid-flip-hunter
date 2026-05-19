# Changelog — Madrid Flip Hunter

## [Unreleased]

### feat: categoría precio_piso en gastos + migration (`db9d628`)

**Problema:** La categoría `compra` agrupaba indistintamente el precio del inmueble y los
gastos auxiliares de compra (notaría, registro, tasas), impidiendo que el P&L distinguiese
cada partida.

**Cambios:**
- `backend/models/operation.py` — Nuevo valor `precio_piso` en el enum `ExpenseCategory`.
  La categoría `compra` queda reservada para gastos auxiliares de compra (notaría, registro, tasas).
- `alembic/versions/a1b2c3d4e5f6_add_precio_piso_to_expensecategory.py` — Migración Alembic:
  `ALTER TYPE expensecategory ADD VALUE IF NOT EXISTS 'precio_piso'`.
- `backend/api/expenses.py` — El resumen de gastos (`summary`) ahora incluye la clave
  `precio_piso`, separada del resto de partidas.
- `frontend/index.html`:
  - Nuevo `<option value="precio_piso">Precio piso</option>` en el select de categoría de
    la tab Gastos (aparece en primera posición).
  - Badge CSS `.cat-precio_piso` (lila oscuro).
  - `CAT_LABELS` actualizado con `precio_piso: 'Precio piso'`.
  - Resumen de gastos: nueva fila "🏠 Precio piso" en primer lugar, visible solo cuando > 0.

---

### fix: P&L precio escritura y ROI desde gastos reales (`9041827`)

**Problema:** Beneficio neto y ROI mostraban `—` cuando el campo manual "Precio escritura"
estaba a 0 en Financiero, aunque el precio estuviese registrado como gasto `precio_piso`.

**Cambios:**
- `backend/api/operations.py`:
  - `_get_expenses_data`: Nuevo bucket `precio_piso` en el dict `by_cat`.
  - `_build_financials_out`:
    - `pp_effective = purchase_price manual` si > 0, si no usa la suma de gastos `precio_piso`.
    - `total_compra` y `total_costes` calculados con `pp_effective`.
    - El response incluye el campo `precio_piso_expenses` para que el frontend lo consuma.
  - `_get_expenses_data` inicializa el dict con `"precio_piso": 0.0`.
- `frontend/index.html` — `calcPnl()`:
  - `ppManual = gv('fin-purchase-price')` (campo manual del formulario).
  - `precioPisoExp = ebc.precio_piso || 0` (suma de gastos con esa categoría).
  - `pp = ppManual > 0 ? ppManual : precioPisoExp` — fallback automático.
  - La línea "Precio escritura" del P&L detallado muestra el valor efectivo.
  - ROI se calcula correctamente cuando el precio viene de gastos.

---

### fix: mejoras generales (`405df25`)

**Bugs corregidos:**

1. **Tramo IRPF 28% para ganancias > 300.000€** — Tanto el motor del backend
   (`_calc_irpf` en `operations.py`) como la función del frontend (`calcIRPF` en
   `index.html`) usaban 27% para todo lo que superase 200.000€. La escala 2024
   de ganancias patrimoniales tiene un tramo adicional:
   - 200.001€ – 300.000€ → 27 %
   - > 300.000€ → 28 %
   Ambas funciones se han actualizado para coincidir con `calcularIRPF` de la
   Calculadora de Viabilidad, que ya era correcta.

2. **Variable `denom` sin uso** — En `calcPnl()` existía `const denom = foc + fib;`
   declarada pero nunca referenciada. Eliminada.

---

## Historial previo

| Commit | Descripción |
|--------|-------------|
| `c403b03` | fix: ITP en sección Compra del P&L, impuestos solo muestra impuestos de venta |
| `498a0d7` | docs: actualizar CONTEXT.md — Deal Tracker módulos 1-7 completos |
| `387ff65` | feat: Calculadora de Viabilidad — análisis único + comparar 3 escenarios |
| `a797355` | fix: calculadora QA — warning % socios se actualiza en tiempo real |
| `beff378` | feat: JWT auth — login requerido para ver listings |
| `4a373b8` | fix: env var names ADMIN_USERNAME/PASSWORD en vez de APP_ |
| `eb5c8ed` | feat: columna Visto (last_seen_at) en tabla listings + KPI último scrape |
| `138d866` | fix: ITP a categoría impuestos, agencia split compra/venta en summary |
| `c96220a` | fix: capital_contributed siempre desde total_costes, notaría venta, formato unificado |
| `e8378fa` | feat: tab Inversores — track record, operaciones cerradas, retorno por inversor |
| `022cc2d` | feat: capital_contributed por socio, formato numérico unificado |
| `03f4eac` | fix: ROI calculation, partners distribution en P&L |
