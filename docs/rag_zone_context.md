# Contexto por zona para el RAG (indicadores del Ayuntamiento)

Estado: R1 (auditoría) y R2 (tabla + importador) hechos. El RAG todavía no lee estas tablas.

## Fuente
Panel de indicadores de distritos y barrios de Madrid (datos.madrid.es, dataset `300087-0-indicadores-distritos`, CC BY 4.0,
actualización anual). CSV acumulado de ~36 MB en formato largo: 392 indicadores, 152 con dato por barrio, ediciones 2020–2026.
Se resuelve la URL con la API CKAN (`/api/3/action/package_show`); `datos.madrid.es` no bloquea a `curl`, a diferencia de `madrid.es`.

## Indicadores importados (lista fija, `backend/market/panel.py`)
| Clave | Indicador del panel | Unidad | Último año |
|---|---|---|---|
| `renta_neta_hogar` | Renta neta media anual de los hogares (Urban Audit) | €/hogar/año | 2023 |
| `valor_catastral_medio` | Valor catastral medio por inmueble de uso residencial | €/inmueble | 2025 |
| `anio_medio_construccion` | Año medio de construcción de inmuebles de uso residencial | año | 2025 |
| `tasa_paro` | Tasa absoluta de paro registrado (febrero) | % | 2026 |
| `crecimiento_poblacion` | Tasa de crecimiento demográfico (porcentaje) | % | 2025 |
| `tamano_medio_hogar` | Tamaño medio del hogar | personas/hogar | 2025 |
| `envejecimiento` | Porcentaje de envejecimiento (población mayor de 65 años/población total) | % | 2025 |
| `locales_cerrados` | Número de locales dados de alta cerrados | locales | 2026 |
| `densidad_poblacion` | Población densidad (hab./ha.) | hab/ha | 2026 |

Sin indicadores de nacionalidad ni de origen de los vecinos: usarlos para valorar una compra puede ser discriminatorio
(Ley 15/2022 de igualdad de trato). La renta y el paro ya recogen lo económico.

## Lo que hay que saber del archivo
- Los códigos vienen sin cero inicial: `cod_barrio` 11 es el barrio `011`, `cod_distrito` 1 es el `01`. Los 131 códigos coinciden
  con los de `zones` (verificado contra el CSV registral, el de población y el geoportal).
- Números con coma decimal y punto de miles (`43,89`, `1.303`, `136.594,48`).
- La unidad se escribe de varias formas (`Media   `, `Proporción`/`Porcentaje`): se guarda la canónica de cada indicador.
- Dos ediciones del panel pueden traer el mismo (indicador, zona, año) con valores distintos (212 casos en el archivo de 2026-10-28):
  vale la edición más nueva. No todos los indicadores tienen todos los años (p. ej. no hay renta 2019).
- Un indicador puede cambiar de nombre entre ediciones: la lista es fija y el importador se niega a seguir si falta alguno.
- Los indicadores de salud, seguridad, calidad de vida, servicios sociales y medio ambiente son solo de distrito.

## Viviendas de uso turístico por barrio (investigado, no implementado)
Dataset `300694-0-viviendas-turisticas-geoportal` (geoportal, CC BY 4.0, bimestral). Trae **1.033 puntos** (shapefile y XLSX;
EPSG:25830, ETRS89 UTM 30N) con distrito (por nombre, escrito de varias formas), dirección, planta, unidades (1.492 en total) y
expediente. **No trae código de barrio.** Cruzado con los polígonos de `Barrios.zip` del geoportal (mismo sistema de coordenadas, sin
reproyectar) con un punto-en-polígono: los 1.033 puntos caen en un único barrio (0 sin barrio, 0 ambiguos), 106 de los 131 barrios tienen
alguno, y solo 1 punto tiene un distrito que no coincide con el del barrio (una dirección de "Distrito de Centro" que cae en Barajas: posible error
del dato). Hace falta una librería geométrica o un lector de shapefile, que hoy no están en las dependencias. No se sabe si el dataset es el
universo de viviendas turísticas o solo las que tienen licencia urbanística; el portal lo titula "con licencia".
