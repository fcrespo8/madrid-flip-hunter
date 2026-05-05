Ver README.md para descripción del proyecto y stack completo.

ESTADO ACTUAL (mayo 2026)
- Scraper Wallapop: funcionando (intercepción XHR de /api/v3/search/section)
- Scraper DonPiso: funcionando (HTML parsing con BeautifulSoup, filtro Madrid capital)
- Scraper Remax: funcionando (HTML parsing con BeautifulSoup)
- Scraper Redpiso: funcionando (HTML parsing con BeautifulSoup)
- Scraper Tecnocasa: funcionando (API JSON interna, Playwright)
- Scraper Idealista: bloqueado por Cloudflare — necesita proxies residenciales
- QA agent: funcionando (filtra alquileres, precios y tamaños anómalos)
- Enrich location agent: funcionando — rellena lat/lon y barrio/distrito por nombre de barrio
- Enrich size agent: funcionando — visita URLs con size_m2=NULL y extrae m² del HTML
- Scoring agent: funcionando, prompt inversor (Carlos Martínez) + contexto precio medio de mercado por barrio (Idealista abr 2026)
- market_prices.py: precios medios €/m² por barrio y distrito (21 distritos, ~130 barrios)
- Soft delete: columnas is_active y last_seen_at — listings sin ver en 30 días se desactivan
- APScheduler: pipeline completo se ejecuta automáticamente cada día a las 7:00 AM
- Dashboard: FastAPI + React + Leaflet en http://localhost:8000 (solo muestra is_active=True)
- CI/CD: GitHub Actions verde (lint ruff + 4 smoke tests)

SCRAPERS ACTIVOS
  poetry run python -m backend.scrapers.run_scrapers   # Wallapop + DonPiso + Remax + Redpiso + Tecnocasa

PIPELINE COMPLETO (ejecutado automáticamente a las 7am vía APScheduler)
  poetry run python -m backend.scrapers.run_scrapers   # scraping + QA + enrich_location + deactivate_stale
  poetry run python -m backend.agents.enrich_size
  poetry run python -m backend.agents.scoring_agent
  poetry run uvicorn backend.api.main:app --reload --port 8000

PROXIMOS PASOS
- Re-scoring completo de listings existentes con nuevo contexto de mercado
- Mejoras dashboard: filtros avanzados, vista comparativa vs precio medio barrio
- Notificaciones: WhatsApp (Twilio) + Email (SendGrid) para score > 7
- README portfolio para mostrar el proyecto
- Deploy cloud: Railway (API + scheduler) + AWS S3 (frontend estático)
