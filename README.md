# Madrid Flip Hunter

Automated pipeline that finds real estate flipping opportunities in Madrid and sends WhatsApp alerts for the best deals.

**Live demo:** [madrid-flip-hunter-production.up.railway.app](https://madrid-flip-hunter-production.up.railway.app)

![CI](https://github.com/fcrespo8/madrid-flip-hunter/actions/workflows/ci.yml/badge.svg)

---

## What it does

Scrapes 5 property portals daily, filters out noise (rentals, commercial properties, anomalous prices), scores each listing against neighborhood market prices, and sends a WhatsApp message when it finds a deal with a score ≥ 7.5/10. Results are visible in a dashboard with an interactive map.

---

## Pipeline

```
Wallapop · DonPiso · Remax · Redpiso · Tecnocasa
              ↓
         QA Agent
   (filters rentals, commercial, price anomalies)
              ↓
     Math pre-scorer
   (price/m² vs neighborhood average)
              ↓
    pre-score < 7 ──→ auto score (no API call)
    pre-score ≥ 7 ──→ Claude scoring (tool use)
              ↓
     WhatsApp alert
   (score ≥ 7.5, first time only)
              ↓
         Dashboard
   (FastAPI + Leaflet, sorted by score)
```

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Scraping | Python + Playwright + playwright-stealth |
| Database | PostgreSQL + SQLAlchemy 2.0 + Alembic |
| AI scoring | Claude API — tool use (structured output) |
| Backend | FastAPI + APScheduler (daily at 7am) |
| Frontend | Vanilla JS + Leaflet.js |
| Alerts | Twilio WhatsApp API |
| Deploy | Railway (API + PostgreSQL) |
| CI/CD | GitHub Actions — lint, tests, auto-deploy on push |

---

## Key technical decisions

**Playwright XHR interception instead of HTML parsing**
The Wallapop scraper intercepts the internal `/api/v3/search/section` call rather than scraping the DOM. Yields structured JSON (price, m², coordinates) directly and is resilient to UI changes.

**Math pre-scorer to minimize Claude API calls**
Before hitting Claude, each listing gets a score based on `price/m² vs neighborhood median` using a static lookup table (~130 neighborhoods, sourced from Idealista). Only listings ≥ 20% below market (pre-score ≥ 7) go to Claude. Reduces API cost significantly without missing real deals.

**Claude tool use for guaranteed structured output**
The scoring agent forces Claude to call `score_listing(score, reasoning, green_flags, red_flags)` via tool use. If it doesn't, the call raises. No regex, no post-hoc parsing — the response is always a valid typed dict.

**Soft delete with `is_active` + `last_seen_at`**
Listings are never hard-deleted. If a listing doesn't appear in any scrape for 30 days, `is_active` is set to `False`. Preserves history, prevents re-alerting on reactivated listings, and keeps the dashboard clean.

---

## Local setup

```bash
git clone https://github.com/fcrespo8/madrid-flip-hunter
cd madrid-flip-hunter
cp .env.example .env        # fill in DATABASE_URL and ANTHROPIC_API_KEY
poetry install
poetry run playwright install chromium
poetry run alembic upgrade head

# Run the pipeline
poetry run python -m backend.scrapers.run_scrapers

# Start the dashboard
poetry run uvicorn backend.api.main:app --reload --port 8000

# Tests
poetry run pytest tests/ -v
```

---

## Deploy

Deployed on Railway with a PostgreSQL addon. The pipeline runs automatically via APScheduler at 7am daily — no external cron needed. GitHub Actions runs lint (ruff) and smoke tests on every push, then deploys to Railway on green.

---

## Author

Francisco Crespo — [github.com/fcrespo8](https://github.com/fcrespo8)
