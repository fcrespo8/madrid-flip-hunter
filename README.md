# 🏠 Madrid Flip Hunter

Sistema multi-agente que detecta oportunidades de flipping inmobiliario en Madrid de forma automatizada.

![CI](https://github.com/fcrespo8/madrid-flip-hunter/actions/workflows/ci.yml/badge.svg)

## ¿Qué hace?

Scrapa pisos de Wallapop, los filtra, y usa Claude (Anthropic) para evaluarlos como un inversor inmobiliario experto. Los resultados aparecen en un dashboard con mapa interactivo ordenados por score de oportunidad.

## Pipeline

Wallapop API → Scraper Agent → QA Agent → PostgreSQL → Scoring Agent (Claude API) → FastAPI → React + Leaflet

## Stack

| Capa | Tecnología |
|------|-----------|
| Scraping | Python + Playwright (intercepción de red) |
| Base de datos | PostgreSQL + SQLAlchemy 2.0 + Alembic |
| AI / Scoring | Claude API (tool use para structured output) |
| Backend | FastAPI |
| Frontend | React + Leaflet.js |
| CI | GitHub Actions |

## Decisiones técnicas destacadas

**Intercepción de API interna de Wallapop** — en lugar de parsear HTML (frágil), el scraper intercepta la llamada a `/api/v3/search/section` con Playwright y captura el JSON directamente. Más robusto ante cambios de UI.

**Tool use para structured output garantizado** — el scoring agent usa function calling de Claude en lugar de parsear texto libre. Garantiza que score, reasoning, green flags y red flags siempre llegan en el formato esperado. Patrón estándar en sistemas de producción con LLMs.

**Migraciones con Alembic** — el schema evoluciona sin tocar la base de datos a mano. Cada cambio es un archivo versionado y reversible.

**Tests sin dependencias externas** — los smoke tests corren sin base de datos real ni API key, usando os.environ.setdefault para aislar el entorno.

## Cómo correrlo

### Requisitos
- Python 3.11, Poetry, PostgreSQL 14

### Setup

    git clone https://github.com/fcrespo8/madrid-flip-hunter
    cd madrid-flip-hunter
    poetry install
    poetry run alembic upgrade head

### Scraping y scoring

    poetry run python -m backend.scrapers.run_scrapers
    poetry run python -m backend.agents.scoring_agent

### Dashboard

    poetry run uvicorn backend.api.main:app --reload --port 8000

### Tests

    poetry run pytest tests/ -v

## Estructura

    backend/
    ├── agents/
    │   ├── qa_agent.py          # filtra alquileres, duplicados y anomalías
    │   └── scoring_agent.py     # Claude API tool use, score 0-10
    ├── models/
    │   └── listing.py           # modelo SQLAlchemy + migraciones Alembic
    ├── scrapers/
    │   └── wallapop_scraper.py  # intercepción de API interna
    └── api/
        └── main.py              # FastAPI + static files
    frontend/
    └── index.html               # React + Leaflet sin build step

## Autor

Francisco Crespo — github.com/fcrespo8
