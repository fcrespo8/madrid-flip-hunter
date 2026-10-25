# Diseño: reemplazo de PRECIOS_MADRID

Estado: **propuesto**. Paso 1 hecho (datos + documentación); nada de lo demás está implementado.
Fecha del diseño: 2026-10-22.

## Decisión

> **Arrancamos solo con el precio registral.** El pre-scorer calcula el descuento contra una única
> referencia: el precio de cierre del Colegio de Registradores por barrio (con caída al distrito),
> actualizado con el IPV del INE. **Idealista se suma después como señal en el dashboard**, no entra
> en el pre-score.

Por qué: el registral es la única fuente con barrio, precio real de cierre y una fuente oficial
descargable; una sola referencia es más fácil de explicar, calibrar y depurar; y las dos
referencias, combinadas con el mínimo, solo cambian el comportamiento donde discrepan más de
15 puntos (17 % de los listings), que es justo lo que conviene mirar en el dashboard antes de
decidir si debe afectar al score.

Fuera de alcance por ahora: cargar el CSV trimestral de Idealista, combinar referencias, y cambiar el
dashboard. Quedan descritos al final como fase 2.

## Punto de partida

`backend/agents/market_prices.py::PRECIOS_MADRID` es un dict estático de ~155 zonas, copiado a mano de
los informes de Idealista (precio de **oferta**, abril 2026). No tiene versión ni fecha por zona, y el
nombre del barrio se resuelve por texto (exacto, normalizado o palabra completa). Consecuencias:

- Los nombres de los portales no son los oficiales; con la tabla actual 252 de 3.280 listings activos no
  resuelven zona.
- El precio de oferta del distrito mide dónde están publicados los pisos, no a cuánto se venden.
- Actualizarla es editar código.

## Datos de partida

`data/raw/precio_registral_barrio.csv` (ver `data/raw/README.md`): precio medio declarado de vivienda
**usada** (€/m²), 2007–2025, por distrito y barrio de la ciudad de Madrid. Datos del Colegio de
Registradores publicados por el Ayuntamiento.

Hechos medidos sobre el archivo y sobre producción (solo lectura, 2026-10-22):

| | |
|---|---|
| Formato | CSV `;`, BOM, `5.333,59`; sin dato = `..` (2007–2016, solo barrios) **o `0,00`** (barrio con < 15 casos) |
| Zonas | ciudad + 21 distritos + 131 barrios; 153 filas por año |
| Sin dato en 2025 | 2 barrios: 027 Atocha y 212 Aeropuerto |
| Segmento | solo usadas, que es el de los flips |
| Resolución sin alias | de 3.280 listings activos no rechazados por QA: 1.657 barrio, 768 distrito, 389 por palabra completa, **466 sin resolver (14 %)** |
| Sin resolver | 147 sin ubicación alguna (no hay alias que valga), San Andrés 73, San Blas 71, Fuencarral 60, Palos de Moguer 28, Concepción 18, Manoteras 9, Sanchinarro 8, Ambroz 8, Las Tablas 7, y municipios que no son Madrid (Coslada, San Sebastián de los Reyes) |

## 1. Esquema

**`zones`** — zonas oficiales.

| Columna | Notas |
|---|---|
| `id` | PK |
| `level` | `ciudad`, `distrito` o `barrio` |
| `code` | código oficial (`04`, `041`) |
| `name` | nombre oficial (`Palos de la Frontera`) |
| `name_norm` | normalizado (sin tildes, minúsculas, sin guiones ni comas) |
| `parent_id` | distrito de un barrio; la ciudad es la raíz |

**`zone_aliases`** — cómo los anuncios llaman a una zona.

| Columna | Notas |
|---|---|
| `alias_norm` | texto normalizado, **único** |
| `zone_id` | zona oficial (NULL si `out_of_scope`) |
| `kind` | `barrio`, `distrito` u `out_of_scope` (otro municipio) |
| `source` | `official` (generado de `zones`) o `manual` |
| `note` | por qué (`Moncloa - Aravaca` es el nombre oficial del distrito 09) |

Los alias manuales viven en `data/aliases.csv` (versionado) y se cargan a la tabla.

**`market_imports`** — una fila por carga: `source`, `file_name`, `sha256`, `period_label`, `loaded_at`,
`row_count`. Una carga con el mismo `sha256` no hace nada.

**`market_prices`** — `zone_id`, `source` (`registral_usadas`), `period` (fecha de fin de período),
`price_m2`, `import_id`. Solo se insertan filas; nunca se actualizan. El precio vigente de una zona es el
del último período. Los valores `..` y `0,00` **no se guardan** (sin fila = sin dato).

**`market_indices`** — `source` (`ine_ipv`), `geography` (`Comunidad de Madrid`), `period` (`2026T2`),
`value`, `loaded_at`.

**En `listings`** (se agrega en modo sombra, ver pasos): `zone_id`, `discount_registral`,
`market_level` (`barrio`/`distrito`: de qué nivel salió la referencia) y `market_version`
(`reg2025+ipv2026T2`). Las columnas actuales `pre_score`, `discount_pct`, `market_zone` se mantienen
hasta el cambio.

## 2. Resolución de zona

`resolve(neighborhood, district)` busca el texto normalizado en `zone_aliases`: primero el barrio, después
el distrito. **Sin match por subcadena** en tiempo de ejecución; lo que antes lo resolvía la subcadena
ahora lo resuelve un alias explícito.

- "Moncloa - Aravaca" → distrito 09 (es el nombre oficial; hoy cae en "Moncloa", 6.376 €/m², cuando
  Aravaca es 5.619).
- "Calle de Ezequiel Solana" → sin zona (hoy, tras el fix de palabra completa, también ninguna).
- Sin dato en el barrio → se usa el distrito, y el listing guarda `market_level = distrito`.
- Sin zona → `unscorable` con motivo `no_zone`; otro municipio → `unscorable` con motivo `out_of_scope`.
  (Hoy ambos casos quedan como `no_market_price`, y los de otro municipio pueden matchear mal.)
- Meta de cobertura: más del 95 % de los listings que traen alguna ubicación.

## 3. Referencia de precio

`R = precio_registral(zona, 2025) × IPV(hoy) / IPV(2025)`

- El INE publica el IPV (índice, no €/m²) con **base 2025 = 100**, por comunidad autónoma y trimestre. El
  factor de actualización es el valor del índice de la Comunidad de Madrid en el último trimestre
  publicado (2T 2026: +12,9 % interanual).
- Supuestos a vigilar: el mismo factor para todas las zonas; el IPV es de toda la comunidad y de todas las
  viviendas, no de la usada de la ciudad; y viene de datos de notarios, el registral de registros (desfase).
- El descuento es `(R − €/m² del anuncio) / R × 100`, positivo = más barato.
- El registral es el promedio de **todas** las usadas del barrio: un piso "a reformar" saldrá barato casi
  siempre. Ya pasaba con la tabla de oferta; hay que tenerlo presente al fijar umbrales.

## 4. Recalibración de los umbrales

Hoy: 30 / 25 / 20 % → pre-score 9 / 8 / 7, que dejan **175 / 272 / 390** candidatos entre los listings
comparables. Claude tiene un tope diario de llamadas, así que los umbrales se fijan por **volumen**, no por
valor absoluto: se eligen los que dejan el mismo número de candidatos.

Medido con 2.241 listings que resuelven en ambas referencias y un factor IPV **supuesto** de 1,13:

| Referencia | umbral 9 | umbral 8 | umbral 7 |
|---|---|---|---|
| Tabla actual (oferta) | 30 | 25 | 20 |
| **Solo registral × 1,13** | **32,8** | **27,6** | **21,5** |

- La mediana del descuento es parecida (tabla actual 0,2 %; registral 1,8 %): el precio de cierre es más
  bajo que el de oferta, pero el dato de 2025 es más viejo, y las dos cosas se compensan.
- Con el **mismo** umbral de 20 % en ambas referencias, de los 390 candidatos actuales 368 también lo son con el registral; 22 dejan de serlo y 68 nuevos entran (con los umbrales recalibrados el volumen se mantiene, pero cambian cuáles).
- Un error de 1 % en el factor de actualización mueve los umbrales ~0,7 puntos. Los números definitivos
  salen con el factor real del INE.
- Los umbrales van **versionados** (`PRE_SCORE_THRESHOLDS`) y un script de calibración imprime el informe
  sin aplicarlo. Cuando llegue el registral 2026 (≈ junio de 2027), el factor se achica y hay que
  recalibrar.
- Antes de fijarlos: revisión a mano de los 30 primeros candidatos nuevos y de los 22 que salen.

## 5. Pasos (cada uno commiteable)

| # | Paso | Cambia comportamiento |
|---|---|---|
| 1 | **Hecho en archivos:** `data/raw/README.md` + este documento. | no |
| 2 | **Hecho:** `backend/market/registral.py`. Parser puro del CSV: BOM, `;`, `5.333,59`, **`..` y `0,00` → sin dato**. Tests con un fragmento real y casos de borde. | no |
| 3 | **Hecho:** migración `e6f1b3d7c2a5` (`zones` + `zone_aliases`) y `scripts/seed_zones.py`, que siembra 153 zonas oficiales y sus alias `official` (idempotente; dry-run por defecto). | no |
| 4 | **Hecho:** `backend/market/resolver.py` (`ZoneResolver`, `load_resolver`, `resolve`) + tests (Moncloa-Aravaca, Ezequiel Solana, San Blas, `out_of_scope`) y el índice parcial `uq_zones_single_ciudad` (migración `f7a2c4e8d9b3`). | no |
| 5 | **Hecho:** `scripts/zone_coverage.py` (solo lectura), `data/aliases.csv` (129 alias manuales) y su carga en `seed_zones`. Cobertura medida el 2026-10-25 sobre producción: 77,0 % → 98,6 % con zona (99,1 % reconociendo otros municipios). Meta > 95 % cumplida. | no |
| 6 | Migración `market_imports` + `market_prices`; importador del registral, idempotente por hash, con tests de recarga e historial. | no |
| 7 | Migración `market_indices`; cargador del IPV del INE (API o archivo; los tests no usan red) y `rollforward_factor`. | no |
| 8 | **Modo sombra:** columnas nuevas en `listings`; el pre-scorer calcula y guarda `zone_id`, `discount_registral`, `market_level`, `market_version` **sin tocar** `pre_score` ni los candidatos. Script que compara ambos sistemas. | no |
| 9 | Script de calibración + umbrales versionados + informe de revisión. | no |
| 10 | El pre-scorer usa la referencia nueva y los umbrales nuevos, detrás de `PRE_SCORER_REFERENCE=legacy\|registral` (default `legacy`). Backfill en dry-run para ver el cambio. | detrás del flag |
| 11 | Cambiar el default a `registral`; el dashboard muestra la zona, el nivel de la referencia y el descuento nuevos. | sí |
| 12 | Borrar `PRECIOS_MADRID` y el matching por texto. | limpieza |
| 13 | Runbook: cargar el registral cada año y el IPV cada trimestre; alerta si la referencia tiene más de N meses. | no |

## Fase 2 (después): Idealista como señal en el dashboard

- Carga manual trimestral de un CSV `periodo,distrito,precio_m2` (los distritos se resuelven con la misma
  tabla de alias; se valida contra el trimestre anterior).
- El dashboard muestra el descuento contra Idealista **al lado** del registral y marca los listings cuyas
  dos referencias discrepan más de 15 puntos (hoy, 17 %).
- Solo si esa señal demuestra ser útil se evalúa combinarla en el pre-score; la combinación candidata es el
  **mínimo** de los dos descuentos (umbrales 29,4 / 24,6 / 18,9 con los datos de hoy).
- Pendiente de validar: los términos de Idealista prohíben el scraping; la carga manual de un informe
  publicado es otro uso y conviene confirmarlo.

## Riesgos

- Los nombres de barrio de los portales no son oficiales: sin la tabla de alias la cobertura se queda en
  86 %.
- Barrios con menos de 15 compraventas (en 2025: Atocha y Aeropuerto) no tienen dato; se cae al distrito.
- El registral es 2025; mientras no haya 2026, todo el precio de referencia depende del IPV.
- Un descuento contra el promedio de usadas del barrio sobrestima el potencial de un piso a reformar.
