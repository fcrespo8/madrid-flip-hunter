# Madrid Flip Hunter

Sistema multi-agente que detecta oportunidades de flipping inmobiliario en Madrid de forma automatizada. Scrapa 5 portales, filtra con QA, pre-puntúa matemáticamente, y llama a Claude solo para los candidatos con potencial real.

![CI](https://github.com/fcrespo8/madrid-flip-hunter/actions/workflows/ci.yml/badge.svg)

**Demo en vivo:** [madrid-flip-hunter-production.up.railway.app](https://madrid-flip-hunter-production.up.railway.app)

---

## El problema

Encontrar un piso para flipear en Madrid implica revisar cientos de anuncios diariamente en múltiples portales, calcular precio/m² por barrio, estimar margen de reforma y filtrar ruido (alquileres, locales, precios anómalos). Es trabajo repetitivo y costoso en tiempo.

## La solución

Un pipeline automático que corre cada día a las 7am, scrapa 5 portales, aplica filtros de calidad, calcula si el precio está por debajo del mercado, y solo llama a Claude para los pisos que matemáticamente tienen potencial. Los deals con score ≥ 7.5 llegan por WhatsApp.

---

## Pipeline

```
Wallapop · DonPiso · Remax · Redpiso · Tecnocasa
         ↓
    QA Agent — filtra alquileres, locales, precios anómalos
         ↓
  Enrich Location — rellena lat/lon y barrio por nombre
         ↓
  Pre-scorer matemático — precio/m² vs media del barrio
         ↓
  ┌─────────────────────────────────────┐
  │  pre-score < 7 → score automático   │  (sin coste de API)
  │  pre-score ≥ 7 → Claude API         │  (solo candidatos reales)
  └─────────────────────────────────────┘
         ↓
  Deactivate Stale — soft delete de listings sin ver > 30 días
         ↓
  WhatsApp Alert (Twilio) — score ≥ 7.5, notified_at IS NULL
         ↓
  Dashboard — FastAPI + Leaflet, ordenado por score
```

---

## Stack

| Capa | Tecnología |
|------|-----------|
| Scraping | Python + Playwright + playwright-stealth |
| Base de datos | PostgreSQL + SQLAlchemy 2.0 + Alembic |
| AI / Scoring | Claude API (Anthropic) — tool use |
| Backend | FastAPI + APScheduler |
| Frontend | Vanilla JS + Leaflet.js |
| Notificaciones | Twilio WhatsApp API |
| Deploy | Railway (API + PostgreSQL + scheduler) |
| CI/CD | GitHub Actions — lint + tests + deploy automático |

---

## Decisiones técnicas

**Intercepción XHR en lugar de parsear HTML**
El scraper de Wallapop usa Playwright para interceptar la llamada interna a `/api/v3/search/section` y captura el JSON directamente. Más robusto que parsear HTML ante cambios de UI, y devuelve datos estructurados (precio, m², coordenadas) sin necesidad de extracción.

**Claude tool use para structured output garantizado**
El scoring agent usa function calling en lugar de parsear texto libre. Claude debe invocar `score_listing(score, reasoning, green_flags, red_flags)` — si no lo hace, el sistema lanza error. Garantiza integridad de datos en producción sin validación post-hoc.

**Pre-scoring matemático para optimizar costes de API**
Antes de llamar a Claude, cada listing recibe un score basado en precio/m² vs media del barrio (datos estáticos de Idealista). Solo los pisos con potencial matemático real (pre-score ≥ 7, es decir, al menos 20% por debajo del mercado) llegan a Claude. Reduce el coste de API drásticamente sin sacrificar calidad en los candidatos.

**Precios de mercado por barrio embebidos**
`market_prices.py` contiene ~130 barrios y 21 distritos de Madrid con precio medio €/m² de Idealista. El lookup es O(1), sin llamadas externas, y se usa tanto en el pre-scorer como en el contexto que recibe Claude y en el dashboard.

**Soft delete con `is_active` + `last_seen_at`**
Los listings no se borran — se marcan como inactivos si no aparecen en ningún scraping en 30 días. Permite análisis histórico y evita re-notificar deals ya vistos.

---

## Cómo correrlo

### Requisitos
Python 3.11, Poetry, PostgreSQL 14

### Setup

```bash
git clone https://github.com/fcrespo8/madrid-flip-hunter
cd madrid-flip-hunter
cp .env.example .env   # rellenar DATABASE_URL y ANTHROPIC_API_KEY
poetry install
poetry run playwright install chromium
poetry run alembic upgrade head
```

### Pipeline completo

```bash
poetry run python -m backend.scrapers.run_scrapers   # scraping + QA + scoring
poetry run uvicorn backend.api.main:app --reload --port 8000
```

### Tests

```bash
poetry run pytest tests/ -v
```

---

## Estructura

```
backend/
├── agents/
│   ├── qa_agent.py            # filtra alquileres, locales, precios anómalos
│   ├── pre_scorer.py          # score matemático precio vs mercado (sin API)
│   ├── scoring_agent.py       # Claude API tool use, score 0-10
│   ├── enrich_location.py     # lat/lon por nombre de barrio
│   ├── deactivate_stale.py    # soft delete listings > 30 días
│   ├── market_prices.py       # precios €/m² por barrio (Idealista abr 2026)
│   └── notifier.py            # alertas WhatsApp via Twilio
├── models/
│   ├── listing.py             # modelo SQLAlchemy
│   └── repository.py          # insert-or-skip con last_seen_at
├── scrapers/
│   ├── wallapop_scraper.py    # intercepción XHR API interna
│   ├── donpiso_scraper.py
│   ├── remax_scraper.py
│   ├── redpiso_scraper.py
│   ├── tecnocasa_scraper.py
│   └── run_scrapers.py        # orquestador del pipeline
└── api/
    └── main.py                # FastAPI + APScheduler (7am diario)
frontend/
└── index.html                 # dashboard: KPIs, tabla vs mercado, mapa Leaflet
```

---

## Autor

Francisco Crespo — [github.com/fcrespo8](https://github.com/fcrespo8)
