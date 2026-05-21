# Madrid Flip Hunter 🏠

An AI-powered real estate deal finder for the Madrid market. Detects underpriced properties with flip potential by scraping multiple portals, enriching listings with market data, and scoring them with a Claude-powered investment analysis agent.

Built as a production-grade portfolio project demonstrating end-to-end AI pipeline design.

---

## What It Does

1. **Scrapes** 5 real estate portals daily (Wallapop, DonPiso, Remax, Redpiso, Tecnocasa)
2. **Filters** non-residential listings and anomalies via a QA agent
3. **Enriches** each listing with GPS coordinates, actual m², and neighborhood market prices
4. **Scores** listings 0–10 using a Claude Sonnet agent with investment reasoning and green/red flags
5. **Alerts** via WhatsApp for listings scoring ≥ 7.5
6. **Displays** results on a dark-theme dashboard with a Leaflet map and sortable table
7. **Tracks** active deals through a full operations management suite (Deal Tracker)

---

## Architecture

```
Daily Pipeline (07:00 AM via APScheduler)
│
├── run_scrapers.py
│   ├── Wallapop      (XHR network interception via Playwright)
│   ├── DonPiso       (Playwright + BeautifulSoup)
│   ├── Remax         (Playwright + BeautifulSoup)
│   ├── Redpiso       (Playwright + BeautifulSoup)
│   └── Tecnocasa     (Playwright + BeautifulSoup)
│       │
│       ├── QA Agent          → filters rentals, garajes, anomalous prices
│       ├── enrich_location   → geocoding (lat/lon)
│       └── deactivate_stale  → soft-delete listings unseen 30+ days
│
├── enrich_size.py            → Playwright scrape of individual pages → regex extract m²
├── enrich_market_prices.py   → €/m² per district (21 distritos, ~130 barrios)
│
└── scoring_agent.py
    ├── Pre-scorer            → math filter: listings ≥20% below market pass through
    └── Claude Sonnet (tool use) → score 0–10 + reasoning + green/red flags
        │
        └── Twilio WhatsApp   → alert if score ≥ 7.5
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 + Poetry |
| Scraping | Playwright + BeautifulSoup4 |
| AI/LLM | Anthropic Claude Sonnet (`claude-sonnet-4-6`) — tool use / function calling |
| Database | PostgreSQL + SQLAlchemy 2.0 + Alembic |
| Backend API | FastAPI |
| Frontend | Single-file SPA (HTML + Leaflet + vanilla JS) |
| Scheduler | APScheduler |
| Notifications | Twilio WhatsApp |
| CI/CD | GitHub Actions (ruff + smoke tests) |
| Containerization | Docker + docker-compose |
| Deployment | Railway |

---

## Key Design Decisions

### Pre-scorer Gate
Before calling the Claude API, a mathematical pre-scorer filters listings by how far below the neighborhood market average they are. Only listings ≥20% below market (pre-score ≥7.0) trigger an LLM call. This keeps token costs low and is architecturally the right separation between cheap heuristics and expensive AI inference.

### Wallapop Network Interception
Instead of scraping HTML, the Wallapop scraper intercepts XHR calls to Wallapop's internal `/api/v3/search/section` endpoint via Playwright, returning clean structured JSON directly.

### Tool Use for Structured Output
The scoring agent uses Claude's function calling (tool use) to enforce a strict JSON schema on every response — score, reasoning, green flags, red flags. This guarantees parseable output without regex hacks.

### Prompt Engineering — "Carlos Martínez"
The scoring agent is prompted as a Madrid real estate investor with 20 years of experience. The persona includes explicit instructions: penalize occupied properties and already-renovated units, treat Wallapop listings as motivated sellers (positive signal), and reward genuine below-market pricing.

---

## Modules

### Deal Finder
The core scraping and scoring pipeline. Exposes a FastAPI REST API consumed by the dashboard.

**Dashboard features:**
- Split table/map view with Leaflet, color-coded pins (green ≥7, yellow 4–6, red <4)
- Filters: score, price, m², €/m², source, sort order
- KPI cards: total listings, avg score, best deal, count below market, last scrape
- Detail panel: reasoning, green/red flags, price vs. market, estimated gross margin
- Admin: mark listing as expired

### Deal Tracker
A full operations management SPA with JWT authentication (admin/viewer roles).

| Tab | Purpose |
|---|---|
| Ficha | Address, neighborhood, m², status timeline (7 milestones), 9 KPI cards |
| Gastos | Line-by-line expenses, 11 categories, CSV export |
| Financiero | Full P&L: purchase → renovation → sale costs → IRPF/IS taxes → net profit |
| Sociedad | Partners per operation, % participation, loan tracking, payout at close |
| Personas | Global partner track record, capital deployed, benefit per person |
| Inversores | Investor-facing view: KPIs, closed deals, pipeline opportunities |

**Calculadora de Viabilidad:** standalone deal analyzer with single-scenario P&L, semáforo ROI (7%/12% thresholds), minimum target price calculator, and 3-column scenario comparator.

---

## Database Schema

### Deal Finder

| Table | Key columns |
|---|---|
| `listings` | source, external_id, price, size_m2, rooms, neighborhood, district, lat, lon, score, score_reasoning, score_green_flags, score_red_flags, is_active, last_seen_at, notified_at |
| `market_prices` | barrio, distrito, price_per_m2 |

### Deal Tracker (9 tables)

| Table | Purpose |
|---|---|
| `operations` | Core record: name, status, address, lat/lon, metros, notes |
| `operation_financials` | Purchase/sale prices, renovation budget, financing |
| `operation_dates` | 7 milestone dates |
| `operation_expenses` | Line items by category (source of truth for all P&L) |
| `operation_partners` | Per-operation: name, role, %, capital, loan |
| `partner_distributions` | Recorded payouts |
| `partners` | Global partner registry |
| `users` | DB users (env-var users bypass DB) |

---

## Local Development

```bash
# Clone and install
git clone https://github.com/fcrespo8/madrid-flip-hunter
cd madrid-flip-hunter
poetry install

# Set up environment
cp .env.example .env
# Fill in: DATABASE_URL, ANTHROPIC_API_KEY, TWILIO_*

# Run migrations
poetry run alembic upgrade head

# Start the API + dashboard
poetry run uvicorn backend.api.main:app --reload --port 8000
```

Open `http://localhost:8000` for the Deal Finder dashboard.  
Open `http://localhost:8000/tracker` for the Deal Tracker.

---

## Running the Pipeline Manually

```bash
# Full scrape + QA + enrichment
poetry run python -m backend.pipeline.run_scrapers

# Enrich m² for listings missing size
poetry run python -m backend.pipeline.enrich_size

# Score all unscored listings
poetry run python -m backend.pipeline.scoring_agent

# Reset and rescore everything
poetry run python -m backend.pipeline.reset_and_rescore
```

---

## Deployment

The project runs on **Railway** with:
- PostgreSQL managed database
- Alembic migrations applied automatically on container startup
- Playwright + Chromium bundled in Docker image
- CI/CD via GitHub Actions on push to `main`

**Production:** `madrid-flip-hunter-production.up.railway.app`

---

## Pending

- [ ] Idealista scraper (blocked by Cloudflare — needs residential proxies)
- [ ] WhatsApp notifier wired into scheduler
- [ ] Deal Tracker: Docs tab (checklist per phase, upload links)
- [ ] Deal Tracker: Recurring expenses UI
- [ ] Deal Tracker: PDF export of P&L for accountant
- [ ] RAG layer: neighborhood qualitative context for scoring agent

---

## Author

Francisco Crespo — Python Backend Engineer transitioning to AI Engineering  
GitHub: [fcrespo8](https://github.com/fcrespo8)
