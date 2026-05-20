# Changelog — Madrid Flip Hunter

## Sesión de revisión y polish (2026-05-19, ~1 hora)

Archivos modificados (sin commit según las instrucciones):
- `frontend/index.html`
- `backend/api/persons.py`
- `backend/api/investor.py`

---

### BUGS corregidos

#### 1. 8 errores silenciados → mensajes visibles al usuario

Todas las funciones `async` que tenían `catch { console.error(...) }` ahora muestran
`toast(...)` con mensaje en español o texto de error visible en la tabla/pantalla.

| Función | Comportamiento anterior | Comportamiento nuevo |
|---------|------------------------|---------------------|
| `loadOperations` | `console.error` silenciado | Fila de error en tabla ("Error cargando operaciones. Inténtalo de nuevo.") |
| `loadFinancials` | `console.error` silenciado | `toast('Error cargando datos financieros', 'error')` |
| `loadExpenses` | `console.error` silenciado | `toast('Error cargando gastos', 'error')` |
| `loadPartners` | `console.error` silenciado | `toast('Error cargando socios', 'error')` |
| `deleteExpense` | `if (!resp.ok) return` sin feedback | `toast('Error al eliminar el gasto', 'error')` + manejo de 401 |
| `deletePartner` | `console.error` silenciado | `toast('Error de conexión al eliminar socio', 'error')` |
| `loadPersonsData` | `console.error` silenciado | `toast('Error cargando datos de personas', 'error')` |
| `loadInvestorData` | `console.error` silenciado | `toast('Error cargando datos de inversores', 'error')` |

#### 2. `deleteExpense` sin manejo de 401

Añadido `if (resp.status === 401) { doLogout(); return; }` para que un token expirado
no deje la UI en estado inconsistente.

#### 3. `addExpense` no validaba la fecha

Antes: si el campo fecha estaba vacío se enviaba `""` al backend, que devolvía 400 de
forma opaca. Ahora hay validación client-side: "La fecha es obligatoria".

#### 4. Emoji duplicado en resumen de gastos

"🏠 Precio piso" y "🏠 Comunidad y otros" usaban el mismo emoji. Corregido a
"🏘 Comunidad y otros".

---

### UX — Feedback visual en formularios

Añadido helper `btnLoading(el, loading, text)` que deshabilita el botón y muestra
"Guardando..." durante la petición, y lo restaura al terminar (tanto en éxito como en error).

Aplicado a:
- **Guardar financiero** (`.fin-save-btn`)
- **Guardar ficha** (`#ficha-edit .btn-primary`)
- **Guardar fechas** (`#dates-edit .btn-primary`)
- **Añadir gasto** (`.expense-form .btn-primary`) — texto loading "Añadiendo..."

Mensajes de toast actualizados con `✓` en los guardados exitosos:
`'Financiero guardado ✓'`, `'Ficha guardada ✓'`, `'Fechas guardadas ✓'`.

---

### UX — Tooltips y placeholders descriptivos

| Campo | Cambio |
|-------|--------|
| Precio escritura | Placeholder `ej. 180000` + tooltip explicando que se puede calcular desde Gastos |
| Precio venta real | Placeholder `ej. 260000` + tooltip indicando que activa el cálculo de beneficio |
| Plusvalía municipal | Tooltip explicando el IIVTNU y cómo afecta al P&L |

---

### Backend — try/except en endpoints de resumen

`backend/api/persons.py` y `backend/api/investor.py` no tenían manejo de errores DB.
Añadido `try/except Exception` alrededor del `db.query(Operation).all()` con:
- `logger.exception(...)` para trazar el error en logs
- `raise HTTPException(status_code=500, detail="Error interno del servidor")`

Antes: un error de DB causaba 500 sin contexto y sin log estructurado.

---

### Pendiente (no llegó el tiempo)

- Loading skeleton para la tab Financiero y Sociedad
- Responsive básico para pantallas < 900px
- Toasts de éxito al eliminar gastos (actualmente la lista se recarga silenciosamente)
- `addPartner` sin loading state en el botón
- Confirmación antes de borrar (gasto, socio)
