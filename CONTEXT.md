Ver README.md para descripción del proyecto y stack completo.

ESTADO ACTUAL (abril 2026)
- Scraper Wallapop: funcionando
- QA agent: funcionando  
- Scoring agent: funcionando, prompt de inversor real (Carlos Martinez)
- Dashboard: FastAPI + React + Leaflet en http://localhost:8000
- CI/CD: GitHub Actions verde (lint ruff + 4 smoke tests)
- ~73 pisos en DB, 28+ con score y reasoning

PROXIMA SESION
- Enrichment agent: precio medio por barrio como contexto para scoring
- Notificaciones: WhatsApp (Twilio) + Email (SendGrid) para score > 7

COMO CORRER
  poetry run python -m backend.scrapers.run_scrapers
  poetry run python -m backend.agents.scoring_agent
  poetry run uvicorn backend.api.main:app --reload --port 8000
  poetry run pytest tests/ -v
