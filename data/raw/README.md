# data/raw

Datos de origen tal como se descargaron. No se editan a mano: si cambia el archivo, se reemplaza
entero y se actualiza el sha256 de este documento.

## precio_registral_barrio.csv

Precio medio declarado de la vivienda usada (€/m²) por distrito y barrio de la ciudad de Madrid,
serie anual 2007–2025.

| | |
|---|---|
| **Fuente** | Ayuntamiento de Madrid, Área de Estadística (Banco de Datos), sección *Edificación y vivienda › Mercado de la vivienda › Compra-venta de viviendas*. Los datos de origen son del **Colegio de Registradores de España** (Estadística Registral Inmobiliaria). |
| **Serie** | "Precio medio declarado de la vivienda (euros/m²) por Distrito y Barrio según Tipo" (serie 6 de esa sección). Esta exportación solo trae el tipo **Usadas**. |
| **Página de la sección** | <https://www.madrid.es/portales/munimadrid/es/Inicio/El-Ayuntamiento/Estadistica/Areas-de-informacion-estadistica/Edificacion-y-vivienda/Mercado-de-la-vivienda/Compra-venta-de-viviendas/> |
| **Fecha de descarga** | 2026-10-22 (fecha del archivo en el repo; la URL exacta de la exportación no quedó registrada). La edición publicada en la página es del 22/06/2026, con datos hasta 2025. |
| **Licencia** | La página del Ayuntamiento no indica una licencia para esta serie. El portal `datos.madrid.es` publica sus conjuntos con CC BY 4.0: citar la fuente (Ayuntamiento de Madrid / Colegio de Registradores de España). Sin confirmar para este archivo. |
| **Tamaño / sha256** | 174.035 bytes · `93f1fea8b24b16fc1b64c0ff53210d9bed4f7f89d67cfd77d6da38a382e966e2` |

Verificar: `shasum -a 256 data/raw/precio_registral_barrio.csv`

### Formato

- UTF-8 **con BOM** (`EF BB BF`), finales de línea `\n`.
- Separador **`;`**. Una cabecera y 2.907 filas de datos.
- Número en formato español: **`5.333,59`** (punto de miles, coma decimal). Hay que convertirlo antes de usarlo.
- **Sin dato**: aparece de dos formas, y las dos significan "no hay valor":
  - **`..`** (1.182 filas): barrios sin publicar. Son los 131 barrios de 2007 a 2015 (solo hay dato por distrito) y 3 más en 2016.
  - **`0,00`** (49 filas): barrio con menos de 15 compraventas ese año. En 2025 son **027. Atocha** y **212. Aeropuerto**.
  Tratar un `0,00` como precio daría un descuento absurdo.

### Columnas

| Columna | Contenido |
|---|---|
| `Distrito` | Distrito con su código: `01. Centro`. La fila de la ciudad es `Ciudad de Madrid`. |
| `Barrio` | Barrio con su código de 3 cifras (`011. Palacio`). En las filas de distrito repite el nombre del distrito. |
| `Año` | 2007–2025. |
| `Precio medio y números índice` | Siempre `Euros/m2` en este archivo. |
| `Tipo de vivienda` | Siempre `Usadas`. |
| `Total` | Valor del año, o sin dato (ver arriba). |

### Estructura

- 153 filas por año × 19 años = 2.907. Cada año: 1 fila de la ciudad + 21 distritos + 131 barrios.
- 153 nombres de zona distintos en total (22 de distrito/ciudad y 131 de barrio).
- Valor de referencia 2025, ciudad de Madrid, usadas: **5.333,59 €/m²**.

### Notas de la fuente (del Ayuntamiento)

- La información recoge el 97,7 % de las transmisiones (compraventas de vivienda con transmisión del 100 % del dominio) de los Registros de la Propiedad referidas al municipio de Madrid.
- El dato por barrio solo se muestra cuando hay al menos 15 casos para calcular la media; el de distrito recoge siempre todos los casos.
- Es un precio **declarado en la compraventa** (cierre), no un precio de oferta.

### Nombres

Los nombres de barrio son los oficiales y **no coinciden siempre con los que usan los portales**:
"Palos de la Frontera" (portales: "Palos de Moguer"), "Ángeles" ("Los Ángeles"), "La Concepción" ("Concepción"),
"San Blas-Canillejas" ("San Blas"), "Villaverde Alto, Casco Histórico de Villaverde" ("San Andrés").
Resolverlos es el trabajo de la tabla de alias (ver `docs/market_prices_design.md`).

## 79563.csv

Índice de precios de vivienda (IPV) del INE, trimestral, **base 2025 = 100**, por comunidad autónoma: tabla **79563** de
INE Base ("Índices de Precios de Vivienda: índices por comunidades autónomas"). Sirve para actualizar el precio registral
de un año a hoy (`backend/market/rollforward.py`). La tabla 25171 es base 2015 y termina en 2025T4: no se usa.

| | |
|---|---|
| **Fuente** | INE (Instituto Nacional de Estadística), IPV. Los datos del INE se pueden reutilizar citando la fuente. |
| **Fecha del archivo** | 2026-10-27 (fecha del archivo en el repo). Períodos: 2025T1–2026T2. |
| **Tamaño / sha256** | 2.252 bytes · `0249739409bac9efca7e7a0292f0563a2034794b6e69e2034793e7bc841d325c` |

Formato: **cp1252** (la "Í" de "Índice" es el byte `0xCD`; no es UTF-8), separador `;`, finales de línea `\r\n`, decimales con
coma (`112,649`). Cabecera:
`Total Nacional;Comunidades y Ciudades Autónomas;General, vivienda nueva y de segunda mano;Índices y tasas;Periodo;Total`.
Las filas con la comunidad vacía son el total nacional; la de Madrid es `13 Madrid, Comunidad de`. Series: `General`,
`Vivienda nueva`, `Vivienda segunda mano`; tipo `Índice`. Un trimestre sin publicar vendría vacío o como `..`.

Valor de referencia: Madrid, vivienda de segunda mano, 2025 (promedio de sus cuatro trimestres) = 100,0 y 2026T2 = 112,649,
es decir un factor 2025 → 2026T2 de **1,12649**.

Cargar: `poetry run python -m scripts.load_ipv --file data/raw/79563.csv` (dry-run; `--confirm` escribe).
