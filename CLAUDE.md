# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
poetry install

# Lint
poetry run ruff check backend/

# Run all tests
poetry run pytest tests/ -v

# Run a single test
poetry run pytest tests/test_smoke.py::test_listing_price_per_m2 -v

# Run pipeline stages manually
poetry run python -m backend.scrapers.run_scrapers
poetry run python -m backend.agents.qa_agent
poetry run python -m backend.agents.scoring_agent

# API server (dev)
poetry run uvicorn backend.api.main:app --reload --port 8000

# Database migrations
poetry run alembic upgrade head
```

## Architecture

**Pipeline**: Wallapop API → Scraper → QA Agent → PostgreSQL → Scoring Agent → FastAPI → React/Leaflet Dashboard

### Scraper (`backend/scrapers/`)
Uses Playwright to intercept Wallapop's internal `/api/v3/search/section` API calls rather than parsing HTML — more resilient to UI changes. Returns `RawListing` objects. Uses `playwright-stealth` to avoid bot detection.

### QA Agent (`backend/agents/qa_agent.py`)
Runs after scraping. Filters out rentals (keyword detection), anomalous prices (50k–2M €), invalid sizes (15–1000 m²), and extreme price/m² ratios. Deletes flagged listings from the DB.

### Scoring Agent (`backend/agents/scoring_agent.py`)
Calls the Claude API with **tool use** (`SCORE_TOOL`) to get guaranteed structured output. Embeds a "Carlos Martínez" persona — an expert real estate flipper with 15 years of experience. Produces: score (0–10), reasoning, green_flags, red_flags. Estimates m² from room count when size is missing.

### Database (`backend/models/`)
SQLAlchemy 2.0 + Alembic migrations. The `listings` table has a unique constraint on `(source, external_id)`. `save_listing()` in the repository handles insert-or-skip logic.

### API + Frontend (`backend/api/main.py`, `frontend/index.html`)
FastAPI serves `/api/listings` (ordered by score descending) and the React frontend as static files. The frontend is a single HTML file — no build step — with a split table/map view. Markers are color-coded: green (score ≥ 7), yellow (4–6), red (< 4).

## Environment

Copy `.env.example` to `.env` and fill in:
- `DATABASE_URL` — PostgreSQL connection string
- `ANTHROPIC_API_KEY` — for the scoring agent

## Tests

Smoke tests in `tests/test_smoke.py` use `os.environ.setdefault()` for mock credentials, so they require no external services. When adding tests, keep them dependency-free in the same style.
