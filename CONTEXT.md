Ver README.md para descripción del proyecto y stack completo.

ESTADO ACTUAL (abril 2026)
- Scraper Wallapop: funcionando (intercepción XHR de /api/v3/search/section)
- Scraper DonPiso: funcionando (HTML parsing con BeautifulSoup, filtro Madrid capital)
- Scraper Idealista: bloqueado por Cloudflare — necesita proxies residenciales
- Fotocasa y Milanuncios: eliminados — bloqueados por Akamai + GeeTest CAPTCHA
- QA agent: funcionando (filtra alquileres, precios y tamaños anómalos)
- Enrich size agent: funcionando — visita URLs con size_m2=NULL y extrae m² del HTML
- Scoring agent: funcionando, prompt de inversor real (Carlos Martinez) con tool use
- Dashboard: FastAPI + React + Leaflet en http://localhost:8000
- CI/CD: GitHub Actions verde (lint ruff + 4 smoke tests)
- BaseScraper: añadido método scrape() como alias de run()

SCRAPERS ACTIVOS
  poetry run python -m backend.scrapers.run_scrapers   # Wallapop + DonPiso

PIPELINE COMPLETO
  poetry run python -m backend.scrapers.run_scrapers
  poetry run python -m backend.agents.enrich_size
  poetry run python -m backend.agents.scoring_agent
  poetry run uvicorn backend.api.main:app --reload --port 8000

PROXIMA SESION
- Enrichment agent: precio medio por barrio como contexto para scoring
- Notificaciones: WhatsApp (Twilio) + Email (SendGrid) para score > 7
- Nuevo scraper: remax.es (sin bot protection, HTML renderizado, pendiente implementar)
