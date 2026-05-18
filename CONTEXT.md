Ver README.md para descripción del proyecto y stack completo.

ESTADO ACTUAL (mayo 2026)
- Scraper Wallapop: funcionando (intercepción XHR de /api/v3/search/section)
- Scraper DonPiso: funcionando (HTML parsing con BeautifulSoup, filtro Madrid capital)
- Scraper Remax: funcionando (HTML parsing con BeautifulSoup)
- Scraper Redpiso: funcionando (HTML parsing con BeautifulSoup)
- Scraper Tecnocasa: funcionando (API JSON interna, Playwright)
- Scraper Idealista: bloqueado por Cloudflare — necesita proxies residenciales
- QA agent: filtra alquileres, precios/tamaños anómalos, y propiedades no residenciales (local, oficina, nave, garaje, trastero, parking, comercial)
- Enrich location agent: funcionando — rellena lat/lon y barrio/distrito por nombre de barrio
- Enrich size agent: funcionando — visita URLs con size_m2=NULL y extrae m² del HTML
- Scoring agent: funcionando, prompt inversor (Carlos Martínez) + contexto precio medio de mercado por barrio (Idealista abr 2026); re-scoring completo ejecutado
- market_prices.py: precios medios €/m² por barrio y distrito (21 distritos, ~130 barrios)
- Soft delete: columnas is_active y last_seen_at — listings sin ver en 30 días se desactivan
- APScheduler: pipeline completo se ejecuta automáticamente cada día a las 7:00 AM
- Dashboard: FastAPI + Leaflet en http://localhost:8000 — KPIs (total, score medio, mejor deal, bajo mercado), columna vs mercado con color, precio medio zona, badges por fuente, toggle "sin m²", panel de detalle con margen estimado
- Deal Tracker (Tab "Operaciones"): Módulos 1-7 completos
  - 8 tablas PostgreSQL con migraciones Alembic
  - Autenticación JWT (admin/viewer)
  - CRUD operaciones con SPA navigation (/operaciones/{id})
  - Tab Ficha: datos generales + timeline de fechas clave
  - Tab Gastos: entrada línea a línea, resumen por categoría, export CSV
  - Tab Financiero: P&L detallado (Inversión → Gastos venta → Impuestos → Beneficio neto), ITP en compra, IRPF con tramos reales, IS 23%, ROI y ROI anualizado
  - Tab Sociedad: socios por operación, % participación, reparto al cierre con tarjetas y gráfico
  - Tab Personas (global): track record por persona, capital aportado, gráfico donut
  - Tab Inversores (global): track record profesional para mostrar en llamadas, KPIs, operaciones cerradas, retorno por inversor, próximas oportunidades
  - Fuente de verdad: P&L calcula todo desde operation_expenses, no desde campos duplicados
  - Datos reales cargados: Ascao 56 (vendido, 44 gastos, 2 socios, beneficio neto ~43.403€)
- CI/CD: GitHub Actions verde (lint ruff + 4 smoke tests) + deploy automático a Railway en cada push a main
- Deploy: Railway — madrid-flip-hunter-production.up.railway.app + PostgreSQL en Railway (DB se puebla con pipeline diario 7am)
- Docker: Playwright + Chromium instalado para scraping en cloud

SCRAPERS ACTIVOS
  poetry run python -m backend.scrapers.run_scrapers   # Wallapop + DonPiso + Remax + Redpiso + Tecnocasa

PIPELINE COMPLETO (ejecutado automáticamente a las 7am vía APScheduler)
  poetry run python -m backend.scrapers.run_scrapers   # scraping + QA + enrich_location + deactivate_stale
  poetry run python -m backend.agents.enrich_size
  poetry run python -m backend.agents.scoring_agent
  poetry run uvicorn backend.api.main:app --reload --port 8000

PROXIMOS PASOS
- Deal Tracker Módulo 8: Calculadora de viabilidad (nueva tab, análisis rápido de pisos, comparación de escenarios)
- Deal Tracker Módulo 9: Documentos (checklist por fase, estado, links)
- Deal Tracker: Deploy a Railway (producción)
- Deal Tracker: Usuario viewer para gestor/socio
- Deal Tracker: Export PDF del P&L para gestoría
- Deal Tracker: Gastos recurrentes automáticos (comunidad mensual, cuota gestoría SL)
- Idealista scraper: pendiente proxies residenciales
- README portfolio actualizado con Deal Tracker
- Cancelar Wix
