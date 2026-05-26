# Madrid Flip Hunter 🏠

AI-powered deal finder for Madrid's residential real estate market. Scrapes five portals daily, filters noise with a QA agent, and scores each listing with a Claude-powered investment analysis agent that combines structured market data with a RAG-retrieved qualitative neighborhood context.

---

## TL;DR

The Madrid real estate market has too many listings and too little signal. This pipeline scrapes ~hundreds of listings per day, filters out rentals/garages/anomalies, and runs a two-stage scoring system: a cheap mathematical pre-scorer gates which listings reach the LLM, and a Claude Sonnet agent with five layers of context produces a structured investment score (0–10) with reasoning, green flags, and red flags. A separate Deal Tracker module manages active operations end-to-end (from prospecto to vendido) with full P&L accounting.

---

## Architecture

```
Daily Pipeline (07:00 AM via APScheduler)
│
├── Scrapers (Playwright + BeautifulSoup)
│   ├── Wallapop      XHR network interception via Playwright
│   ├── DonPiso       HTML parsing
│   ├── Remax         HTML parsing
│   ├── Redpiso       HTML parsing
│   └── Tecnocasa     Internal JSON API via Playwright
│
├── QA Agent          Filter rentals, garages, anomalous prices/sizes
├── enrich_location   Geocode lat/lon + normalize neighborhood/district names
├── enrich_size       Playwright re-visit of individual pages → regex extract m²
├── deactivate_stale  Soft-delete listings unseen for 30+ days (is_active flag)
│
├── Pre-scorer (mathematical gate)
│   └── price/m² vs market_prices.py dict → score 1–9
│       └── score >= 7.0 → send to Claude  (score < 7.0 → assigned directly, no LLM call)
│
└── Scoring Agent (Claude Sonnet)
    ├── [1] Structured listing data        (scraper → DB)
    ├── [2] Market price lookup            (market_prices.py dict — not RAG)
    ├── [3] RAG neighborhood context       (pgvector cosine similarity)
    ├── [4] System prompt / persona        (Carlos Martínez investor agent)
    └── [5] SCORE_TOOL                     (Anthropic function calling → structured JSON)
        │
        └── score >= 7.5 → Twilio WhatsApp alert
```

---

## Scoring Agent — Five Context Layers

Every scored listing sends Claude a message assembled from five distinct layers:

### Layer 1 — Structured listing data
Title, price, size_m2, price/m², rooms, neighborhood, district, source portal, and the raw scraped description. Assembled as plain text from the `listings` table.

### Layer 2 — Market price lookup (deterministic, not RAG)
`market_prices.py` is a static Python dict with ~130 Madrid barrios and 21 distritos, priced in €/m² from Idealista (last update: April 2026). The lookup is a plain `dict.get(barrio)` — no embeddings, no retrieval, no semantic matching. If the barrio key is missing, the field is omitted. This is a feature, not a retrieval system.

```
Precio medio del barrio: 3.718€/m² (media del barrio según Idealista abr 2026)
Diferencia vs mercado: -22.4% vs media
```

### Layer 3 — Qualitative neighborhood context (RAG)
Retrieved at score time from a `neighborhood_docs` table in PostgreSQL via pgvector. Each doc is a qualitative description of a Madrid barrio: buyer profile, gentrification trend, market dynamics, renovation potential. See the RAG section below for implementation details.

### Layer 4 — Persona and system prompt
Carlos Martínez — 15 years flipping in Madrid, 80+ operations. The system prompt encodes concrete scoring heuristics: price/m² bands, signals of motivated sellers ("herencia", "urgente", "a reformar"), renovation upside logic, and neighborhood-specific penalties (no elevator on 4th+, tenanted, already renovated).

### Layer 5 — SCORE_TOOL (function calling)
The agent uses Anthropic's tool use (function calling) to enforce a strict JSON schema on every response. No regex hacks, no prompt fragility. The schema requires `score` (float 0–10), `reasoning` (string), `green_flags` (array), `red_flags` (array). If Claude doesn't return a `tool_use` block, the call raises — it never silently writes a malformed score.

---

## RAG Implementation

### Vector store
pgvector extension on the existing Railway PostgreSQL instance. No separate vector database. Same connection string, same SQLAlchemy session, ACID transactions alongside the rest of the data. The `neighborhood_docs` table has an HNSW index on a `Vector(384)` column.

```python
# backend/models/neighborhood_doc.py
embedding = Column(Vector(384), nullable=False)  # pgvector
```

### Embeddings
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` — 384-dimensional, multilingual, runs on CPU, zero inference cost. Loaded as a lazy singleton via `lru_cache`. Embeddings are L2-normalized before storage (`normalize_embeddings=True`), so cosine distance reduces to inner product at query time.

### Corpus
~30 qualitative neighborhood documents covering Madrid's main barrios. The corpus was generated using the Claude API (seed script: `scripts/seed_neighborhood_docs.py`), then embedded with sentence-transformers and upserted into the `neighborhood_docs` table. Documents contain: buyer profile, gentrification stage, renovation potential, demand patterns, typical transaction times.

### Retrieval strategy
Hybrid metadata + vector search (not BM25 + vector). Two-stage:

1. **District filter first** — `WHERE distrito = ?` on the `NeighborhoodDoc` table. Structured metadata lookup for precision; avoids retrieving docs from unrelated districts.
2. **Cosine similarity within filter** — `.order_by(NeighborhoodDoc.embedding.cosine_distance(query_vector)).limit(2)`
3. **Global fallback** — if the district has no docs, drop the filter and search globally by cosine similarity.

```python
# backend/rag/retrieval.py
query_vector = embed_text(
    f"Barrio {barrio} en el distrito {distrito} de Madrid. "
    f"Características del barrio, gentrificación, perfil de comprador, "
    f"potencial de inversión inmobiliaria."
)

results = session.query(NeighborhoodDoc)
    .filter(NeighborhoodDoc.distrito == distrito)        # metadata filter
    .order_by(NeighborhoodDoc.embedding.cosine_distance(query_vector))
    .limit(top_k)
    .all()

if not results:  # global fallback
    results = session.query(NeighborhoodDoc)
        .order_by(NeighborhoodDoc.embedding.cosine_distance(query_vector))
        .limit(top_k)
        .all()
```

The retrieved docs are injected into the scoring agent message as a labeled block:
```
CONTEXTO CUALITATIVO DEL BARRIO (de tu base de conocimiento):
[Carabanchel - Carabanchel]
...
```

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 + Poetry |
| Scraping | Playwright (stealth) + BeautifulSoup4 |
| AI / LLM | Anthropic Claude Sonnet (`claude-sonnet-4-6`) — async client, tool use |
| RAG embeddings | sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2` (384-dim) |
| Vector store | pgvector on PostgreSQL (HNSW index) |
| Database | PostgreSQL + SQLAlchemy 2.0 + Alembic (13 migrations) |
| Backend API | FastAPI + JWT auth (admin / viewer roles) |
| Frontend | Single-file SPA — HTML + Leaflet + vanilla JS, no build step |
| Scheduler | APScheduler (daily 07:00) |
| Notifications | Twilio WhatsApp |
| CI/CD | GitHub Actions (ruff lint + smoke tests) → deploy to Railway |
| Containerization | Docker + Playwright + Chromium |
| Deployment | Railway (web + managed PostgreSQL) |

---

## Design Decisions

### pgvector vs Pinecone / Qdrant / Chroma
pgvector runs on the existing Railway PostgreSQL — same connection string, same SQLAlchemy session, ACID transactions alongside listing and operation data. A separate vector database would add a second service to maintain, a second billing line, and network latency on every score call. At corpus size < 100k documents, the HNSW index in pgvector has equivalent query performance to dedicated solutions. The operational cost of adding infrastructure is not justified at this scale.

### sentence-transformers vs paid embeddings (OpenAI, Voyage AI)
`paraphrase-multilingual-MiniLM-L12-v2` is multilingual, runs on CPU, costs nothing per inference, and reaches sufficient quality for 30 Spanish neighborhood descriptions. Paid embedding APIs (OpenAI text-embedding-3, Voyage AI) would improve retrieval precision meaningfully only at corpus sizes where semantic distinctions between similar neighborhoods matter — not at 30 docs. The model loads once via `lru_cache` and stays in memory for the lifetime of the process.

### Metadata-first hybrid retrieval vs full vector search
Full vector search across all neighborhood docs risks retrieving a semantically similar-but-wrong neighborhood (e.g., a Málaga barrio if the corpus ever expands). The district filter enforces geographic coherence as a hard constraint before ranking by similarity. "Hybrid" here means metadata filter + vector similarity, not BM25 + vector (no inverted index is involved).

### Tool use vs JSON-in-prompt for structured output
`tool_choice` with a strict schema forces Claude to call `score_listing` or fail — there is no fallback to unstructured prose. JSON-in-prompt approaches break under context pressure or when the model hedges. The scoring agent's response parsing is a single `if block.type == "tool_use"` check; if it's not there, the call raises immediately rather than silently writing a malformed score to the database.

### Pre-scorer gate before LLM calls
Every listing that enters the database gets a mathematical pre-score based on price/m² vs neighborhood market average. Only listings where `pre_score >= 7.0` (≥20% below market) trigger a Claude API call. Listings below that threshold are assigned the pre-score directly with a one-line reasoning string. This separates cheap heuristics from expensive inference and keeps API costs proportional to deal quality, not volume.

---

## Limitations and Next Iteration

**Cold start on proprietary comparables.** The most valuable RAG enhancement would be few-shot injection of historical flip operations: "similar barrio, similar m², bought at X€, renovated for Y€, sold in Z months at W€ ROI." The Deal Tracker has one real closed operation (Ascao 56). This becomes useful at ~5–10 closed deals; it's not worth implementing yet.

**No validation metric.** The natural way to evaluate scoring quality is correlation between the agent's score and actual ROI on purchased listings. That requires a closed-deal dataset that doesn't exist yet. Right now, calibration is purely qualitative (does the score feel right to an experienced investor?).

**Normativa urbanística not indexed.** Zonas tensionadas under the 2023 Ley de Vivienda, mandatory ITE by construction year, and IBI coefficients by district are identified as the highest-value corpus addition. A listing in a zona tensionada has real legal constraints on profitability that the agent currently can't reason about. The corpus is defined; the indexing work is pending.

**Idealista scraper blocked.** Cloudflare protection requires residential proxies. Idealista has the highest-quality listings and the richest metadata. Currently absent from the scraping pipeline.

**Market prices are static.** `market_prices.py` is updated manually every ~2 months. A dynamic corpus indexed from public Catastro or Registradores data would be a meaningful upgrade, but the manual update cadence is sufficient for current usage.

---

## Modules

### Deal Finder
The core scraping and scoring pipeline. Exposes a FastAPI REST API consumed by the dashboard.

**Dashboard features:**
- Split table/map view, Leaflet pins color-coded by score (green ≥7, yellow 4–6, red <4)
- Selected pin highlights on row click (enlarged radius, fly-to animation)
- Filters: score, price, m², €/m², source, sort order, hide listings without m²
- KPI cards: total listings, avg score, best deal, count below market
- Detail panel: reasoning, green/red flags, price vs. market, estimated gross margin
- Admin: mark listing as expired

### Deal Tracker
A full operations management SPA with JWT authentication (admin/viewer roles). Accessible at `/tracker`.

| Tab | Purpose |
|---|---|
| Ficha | Address, neighborhood, m², status timeline (7 milestones), 9 KPI cards |
| Gastos | Line-by-line expenses by category (11 categories), CSV export |
| Financiero | Full P&L: purchase → renovation → sale costs → IRPF/IS taxes → net profit. Source of truth: `operation_expenses` table, not duplicated fields |
| Sociedad | Partners per operation, % participation, loan tracking, payout at close |
| Personas | Global partner track record, capital deployed, net benefit per person |
| Inversores | Investor-facing view: KPIs, closed deals, return per investor, pipeline |

**Calculadora de Viabilidad:** standalone deal analyzer. Single-scenario P&L, semáforo ROI thresholds (< 7% red / 7–12% yellow / > 12% green), minimum sale price calculator given a target ROI, and 3-column scenario comparator.

---

## Database Schema

### Deal Finder

| Table | Key columns |
|---|---|
| `listings` | source, external_id, price, size_m2, rooms, neighborhood, district, lat, lon, score, score_reasoning, score_green_flags, score_red_flags, is_active, last_seen_at, notified_at |
| `neighborhood_docs` | barrio, distrito, content, embedding Vector(384) |

### Deal Tracker (9 tables)

| Table | Purpose |
|---|---|
| `operations` | Core record: name, status (6 states), address, lat/lon, metros, notes |
| `operation_financials` | Purchase/sale prices, renovation budget, financing terms |
| `operation_dates` | 7 milestone dates (offer → arras → escritura → renovation → listing → sale) |
| `operation_expenses` | Line items by category — source of truth for all P&L calculations |
| `operation_partners` | Per-operation: name, role, % participation, capital, loan |
| `partner_distributions` | Recorded payouts at close |
| `partners` | Global partner registry |
| `recurring_expenses` | Recurring costs (comunidad, gestoria) — table exists, UI pending |
| `users` | DB users with role (admin/viewer) |

---

## Local Development

```bash
git clone https://github.com/fcrespo8/madrid-flip-hunter
cd madrid-flip-hunter
poetry install

cp .env.example .env
# Fill in: DATABASE_URL, ANTHROPIC_API_KEY, TWILIO_* (optional)

# Apply migrations (includes pgvector neighborhood_docs table)
poetry run alembic upgrade head

# Seed the RAG knowledge base (~30 neighborhood docs, requires ANTHROPIC_API_KEY)
poetry run python scripts/seed_neighborhood_docs.py

# Start API + dashboard
poetry run uvicorn backend.api.main:app --reload --port 8000
```

- Deal Finder dashboard: `http://localhost:8000`
- Deal Tracker: `http://localhost:8000/tracker`

---

## Running the Pipeline Manually

```bash
# Full scrape + QA + enrichment + scoring
poetry run python -m backend.scrapers.run_scrapers

# Enrich m² for listings missing size (Playwright re-visit)
poetry run python -m backend.agents.enrich_size

# Re-score all listings (wipes existing scores, reruns Claude on all)
poetry run python -m backend.agents.reset_and_rescore
```

---

## Deployment

Runs on **Railway** with:
- PostgreSQL managed database (pgvector extension enabled)
- Alembic migrations applied automatically on container startup
- Playwright + Chromium bundled in Docker image
- GitHub Actions CI on every push to `main`: ruff lint + smoke tests → auto-deploy

**Production:** `madrid-flip-hunter-production.up.railway.app`

---

## Pending

- [ ] Idealista scraper (blocked by Cloudflare — needs residential proxies)
- [ ] Deal Tracker: Docs tab (checklist per phase, upload links)
- [ ] Deal Tracker: Recurring expenses UI
- [ ] Deal Tracker: PDF export of P&L for accountant
- [ ] RAG: Index normativa urbanística (zonas tensionadas, ITE, IBI by district)
- [ ] RAG: Historical operation comparables once 5+ deals are closed

---

## Author

Francisco Crespo — AI / Backend Engineer  
GitHub: [fcrespo8](https://github.com/fcrespo8)
