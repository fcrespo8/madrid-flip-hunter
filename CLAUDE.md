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
poetry run python -m backend.scoring.runner          # scores pending active listings (calls Claude)

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

### Scoring (`backend/scoring/`)
Single module; public entry point is `runner.run_scoring(listings=None, db=None)`. Without `listings` it scores pending active listings (`score IS NULL`); without `db` it opens its own session. It does **not** notify — `run_scrapers.run_all` calls it on pre-score candidates, then `notify_scored()` sends WhatsApp for newly saved scores ≥ 7.5.
- `prompt.py` — `SYSTEM_PROMPT` ("Carlos Martínez" flipper persona), `SCORE_TOOL`, `MODEL_ID` (env `SCORING_MODEL_ID`, default `claude-sonnet-4-6`), pure `build_listing_context()`.
- `client.py` — lazy `AsyncAnthropic` singleton, `request_score()` with `tool_choice` forced to `score_listing`, strict `validate_score_result()` (required fields, score 0–10). Typed errors: `LLMCallError`, `NoToolUseError`, `ScoreValidationError`.
- `rag.py` — neighborhood context from pgvector; on failure rolls back the session and continues without context.
- `graph.py` — LangGraph `retrieve_rag → score → validate → save`; any error ends the graph without saving. Langfuse spans: `score_listing` (root), `retrieve_rag`, `llm_score`.
- `backend/agents/reset_and_rescore.py` — dry-run by default; `--confirm` resets score + `scored_at` on active listings and rescores; `--limit N`.
- Tests in `tests/test_scoring.py` inject a fake client/retriever/DB — never call the real API in tests.

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
