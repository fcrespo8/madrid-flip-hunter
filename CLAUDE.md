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
Uses Playwright to intercept Wallapop's internal `/api/v3/search/section` API calls rather than parsing HTML — more resilient to UI changes. Uses `playwright-stealth` to avoid bot detection.

Every scraper returns a `ScrapeResult` (`base_scraper.py`): the `RawListing`s plus `complete`, `total_reported`, `incomplete_reason` and `error`. Build it with `ScrapeResult.finish(...)`, which holds the rule: `complete=True` only if the sweep reached the natural end of the catalog (not a page cap, a repeated page, an error or a single-page read), found at least one listing, and — when the site reports a total — saw ≥ 90 % of it (counting the items the site showed, before our own filters). `deactivate_stale` only receives sources with `complete=True` **and** `supports_deactivation` (a class attribute on the scraper, default `True`; `scrape_runs` and `source_stats` keep the truthful `complete`), because "not seen" means "no longer published" only after a full sweep. Every sweep is recorded in `scrape_runs` (`record_scrape_run`, which never raises).

Pagination status: **tecnocasa** walks every page up to the `total_pages` the API reports (a 200-page safety ceiling aside), waits 1 s between pages, retries a failing page twice with 2 s / 4 s backoff (not 4xx other than 408/429), and can reach `complete=True`. A cut by HTTP failure keeps what it saw and records `incomplete_reason="http_error"` with the page in `error`; an API reply without a valid `total_pages` records `bad_pagination` (never assumed to be the last page). **donpiso** uses plain HTTP (no Playwright; the site serves its HTML ready-made) and walks `/listado` then `pagina-N.html` with the same delay, retry and 50-page ceiling. It stops at the natural end only when every signal agrees — no `<link rel="next">`, no body links to later pages, and the title's "(pag. N de M)" matches N = M. A contradiction, or an empty page after a page that promised another (the site answers out-of-range pages with an empty HTTP 200, not a 404), records `bad_pagination`. The site shows no listing total, so `total_reported` is null. **wallapop** loads the search in headless Chromium with an explicit normal `user_agent` (without it the `User-Agent` header says `HeadlessChrome` and CloudFront answers 403 before any JS runs; stealth only patches `navigator.userAgent`), takes the first `/api/v3/search/section` response and then follows `meta.next_page` with an in-page `fetch` (`?next_page=<token>`, reusing the app's `x-deviceid`/`mpid`/`x-appversion`… headers) with the same delay, retry and 200-page ceiling. The natural end is `meta: {"next_page": null}`; a response without `meta` records `bad_pagination`. The app ignores the `latitude`/`longitude` of the search URL (Madrid or Barcelona, it asks the API for 40.4252/-3.6927 either way), so the request to `search/section` is rewritten with `page.route` to pin Madrid's centre (`MADRID_LAT`/`MADRID_LON`). Whether the Railway datacenter IP was served an empty list because of geolocation is unverified. Failures are told apart: `blocked` (401/403/429, e.g. the CloudFront WAF), `no_api` (page loaded but no `search/section` call), `empty_first_page` (HTTP 200 but page 1 has no items with an `id`; the log line `Primera página sin anuncios` gives item count, request lat/lon and `meta`/body keys; every first response is logged at INFO as `Primera respuesta`), `http_error`, `unparseable_page`. It sets `supports_deactivation = False`: it is a keyword/relevance search (about 2,270 results in 61 pages), not a catalog, so a listing missing from the results may just have left the ranking — a complete sweep is recorded but never used to deactivate. The API gives no total (`total_reported` null). `size_m2` comes from `type_attributes.surface` (on about 42% of the items; it looks like usable area: when the description quotes a different figure it is usually the built area, 10-50% larger) and, when that is missing, from the first plausible figure (15-1000 m²) in the description that is not a terrace, living room, storage room, etc. (`parse_size_from_description`; about 76% total coverage on real data, and conservative: no value is preferred over a wrong one). The rest go through `enrich_sizes`. Still not fixed: redpiso and remax ignore their page parameter (a repeated page is detected and stops the walk) — they cannot be `complete`, so their listings are never auto-deactivated. Check `scrape_runs.incomplete_reason`.

### QA Agent (`backend/agents/qa_agent.py`)
Runs after scraping (and after `deactivate_stale`) on pending listings. Flags rentals (keyword detection), non-residential listings, anomalous prices (50k–2M €), invalid sizes (15–1000 m²), and extreme price/m² ratios. It **marks, never deletes**: sets `qa_rejected = true` and `qa_reason` (issues joined with `"; "`). Rejected listings stay excluded from the pipeline even if they're scraped again.

### Score lifecycle (`listings.score_status`)
- `pending` — new or reset; the only status the pipeline picks up (`repository.pending_listings_query`: `pending AND NOT qa_rejected AND is_active`).
- `auto` — scored by `pre_scorer.apply_pre_scores` without Claude (pre-score < 7).
- `llm` — scored by Claude (pre-score ≥ 7 candidates). `apply_pre_scores` returns the candidates ordered by pre-score descending, then by discount vs the market €/m² (`discount_pct`, bigger first), then by `id` (oldest first), so `max_llm_calls` / `PIPELINE_MAX_LLM_CALLS` takes the best N and defers the rest (still `pending`). Caveat: a steady stream of 8–9 pre-scores can keep a 7 deferred indefinitely.
- `unscorable` — pre-score can't run; `score_status_reason` is `no_price`, `no_size` or `no_market_price`. Only exit: `enrich_sizes` moves `no_size` back to `pending` when it fills `size_m2` (15–1000 m²). If the page downloads fine but has no usable size, the reason becomes `no_size_final` and it's never retried (set it back to `no_size` to force a retry); failed downloads (an exception, HTTP >= 400 such as a CloudFront 403 page, or an empty reply) don't count: the listing is left untouched and retried on a later run, never marked `no_size_final`. `enrich_sizes` skips sources whose scraper sets `supports_size_enrichment = False` (class attribute, default `True`; wallapop is the only one: its item pages gave no m² in 0 of 10 tries, and its m² come from `surface`/description at scrape time). The list comes from `scrapers/registry.py` (`SCRAPER_CLASSES` must match what `run_all` runs; a test checks it). Those listings stay `unscorable`/`no_size`. The `enrich_sizes` browser sends a normal `user_agent` (headless Chromium's default says `HeadlessChrome`).
- `failed` — Claude failed 3 times (`score_attempts`, incremented before each call); reason holds the last error. `reset_and_rescore` resets these to `pending` with 0 attempts.

A DB `CHECK` constraint enforces these five values.

### Scoring (`backend/scoring/`)
Single module; public entry point is `runner.run_scoring(listings=None, db=None)`. Without `listings` it scores `pending_listings_query`; without `db` it opens its own session. It does **not** notify — `run_scrapers.run_all` calls it on pre-score candidates, then `notify_scored()` sends WhatsApp for newly saved scores ≥ 7.5.
- `prompt.py` — `SYSTEM_PROMPT` ("Carlos Martínez" flipper persona), `SCORE_TOOL`, `MODEL_ID` (env `SCORING_MODEL_ID`, default `claude-sonnet-4-6`), pure `build_listing_context()`.
- `client.py` — lazy `AsyncAnthropic` singleton, `request_score()` with `tool_choice` forced to `score_listing`, strict `validate_score_result()` (required fields, score 0–10). Typed errors: `LLMCallError`, `NoToolUseError`, `ScoreValidationError`.
- `rag.py` — neighborhood context from pgvector; on failure rolls back the session and continues without context.
- `graph.py` — LangGraph `retrieve_rag → score → validate → save`; any error ends the graph without saving. Langfuse spans: `score_listing` (root), `retrieve_rag`, `llm_score`.
- `backend/agents/reset_and_rescore.py` — dry-run by default; `--confirm` resets score + `scored_at` on active listings and rescores; `--limit N`.
- Tests in `tests/test_scoring.py` inject a fake client/retriever/DB — never call the real API in tests.

### Database (`backend/models/`)
SQLAlchemy 2.0 + Alembic migrations. The `listings` table has a unique constraint on `(source, external_id)`. `save_listing()` in the repository handles insert-or-skip logic, and reactivates a listing (`is_active = true`) when the scraper sees it again. `scrape_runs` has one row per source sweep (`seen_count`, `new_count`, `complete`, `total_reported`, `incomplete_reason`, `error`).

### Pre-score fields (`listings.pre_score`, `discount_pct`, `market_zone`)
`pre_scorer.apply_pre_scores` stores them on every listing it processes, on every run (`store_pre_score_fields`): the pre-score (1/3/5/7/8/9), the discount % under the market €/m² (positive = cheaper, negative = dearer) and the `PRECIOS_MADRID` zone used as reference (`market_prices.match_market_zone` returns zone, price and match type exacto/normalizado/parcial; `get_market_price` is built on it and gives the same prices as before). Unscorable listings keep what can be computed (zone without size, nothing without a zone). `score` stays the final grade: the pre-score for `auto`, Claude's for `llm`. Listings scored before the migration have NULLs until `poetry run python -m scripts.backfill_pre_scores` (dry-run by default, `--confirm` writes only these three columns, `--all` recomputes everything).

### API + Frontend (`backend/api/main.py`, `frontend/index.html`)
FastAPI serves `/api/listings` (active, non-QA-rejected listings with a pre-score or a score; `?sort=pre_score` (default) orders by pre-score then discount, `?sort=score` by final grade; each row has `pre_score`, `discount_pct`, `market_zone`, `claude_score`, `has_claude`, `score_status`; `claude_score` is `score` only when `score_status='llm'`) and the React frontend as static files. The frontend is a single HTML file — no build step — with a split table/map view. Markers and the first column use the pre-score (green ≥ 7, yellow 4–6, red < 4, grey if none); listings analysed by Claude show ⭐ and its grade; the default order is pre-score then discount.

## Environment

Copy `.env.example` to `.env` and fill in:
- `DATABASE_URL` — PostgreSQL connection string
- `ANTHROPIC_API_KEY` — for the scoring agent

## Tests

Smoke tests in `tests/test_smoke.py` use `os.environ.setdefault()` for mock credentials, so they require no external services. When adding tests, keep them dependency-free in the same style.
