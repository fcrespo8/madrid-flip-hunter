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
- README portfolio para mostrar el proyecto
- Cancelar Wix
