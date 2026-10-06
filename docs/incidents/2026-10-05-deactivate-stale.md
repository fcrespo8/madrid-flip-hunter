# Incidente 2026-10-05: listings desactivados en masa

## Qué pasó
En la primera corrida real de `run_pipeline` el scraper falló (Chromium no instalado
en local) y no trajo ningún listing. Igual corrió `deactivate_stale`, que desactiva
lo no visto en 30 días. Como en producción no se scrapeaba desde el 29/06, desactivó
964 listings activos y el dashboard quedó vacío. Sin costo de Claude ni WhatsApp.

## Arreglo
- `deactivate_stale` solo actúa sobre fuentes que se scrapearon bien en la corrida
  (sin error y con al menos un listing). Sin fuentes OK, no desactiva nada.
- `save_listing` vuelve a poner `is_active=True` cuando un listing reaparece.
- Datos recuperados con un `UPDATE` por `last_seen_at`, previo `SELECT count(*)`
  y dentro de una transacción (973 filas).
- Verificado el 06/10: corrida con donpiso desactivó solo 14 listings de donpiso.
