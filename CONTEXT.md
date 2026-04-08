━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MADRID FLIP HUNTER — AI ENGINEERING PORTFOLIO PROJECT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PERFIL: Software engineer aprendiendo AI Engineering.
Objetivo paralelo: portfolio para entrevistas AI Engineer en ~2 semanas.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUÉ ES EL SISTEMA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sistema multi-agente en Python que detecta oportunidades de
flipping inmobiliario en Madrid de forma automatizada.

Pipeline de agentes:
  1. Scraper agent    → ✅ extrae pisos de Wallapop vía API interna interceptada
  2. QA agent         → ✅ elimina duplicados, alquileres, anomalías
  3. Enrichment agent → precio/m² zona, transporte (PENDIENTE)
  4. Scoring agent    → ✅ Claude API, score 0-10 con tool use (reasoning se pierde, ver PRÓXIMA SESIÓN)

Outputs:
  - Notification agent → WhatsApp (Twilio) + Email (SendGrid) (PENDIENTE)
  - Dashboard          → FastAPI + React + Leaflet.js (PENDIENTE)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STACK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Python 3.11, PostgreSQL 14, SQLAlchemy 2.0, Alembic
Playwright, BeautifulSoup, playwright-stealth 2.x
FastAPI, React, Leaflet.js
Claude API (claude-sonnet-4-20250514) via anthropic SDK
APScheduler, Docker, GitHub Actions, Railway
Twilio (WhatsApp), SendGrid (Email)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ENTORNO LOCAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mac, Python 3.11, Poetry 2.3.2, Git
PostgreSQL 14 corriendo en localhost:5432
DB: madrid_flip_hunter
Usuario DB: franciscocrespo
Repo: https://github.com/fcrespo8/madrid-flip-hunter
Ruta local: /Users/franciscocrespo/dev/github/madrid-flip-hunter

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ESTRUCTURA DE ARCHIVOS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
madrid-flip-hunter/
├── .env                          # DATABASE_URL + ANTHROPIC_API_KEY (no en git)
├── .gitignore
├── pyproject.toml                # Poetry deps
├── alembic.ini
├── CONTEXT.md
├── README.md
│
├── alembic/
│   ├── env.py                    # conectado a Base + Listing
│   └── versions/
│       └── afb84d6a4a3d_create_listings_table.py
│
├── backend/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── qa_agent.py           # ✅ valida, elimina alquileres y anomalías
│   │   └── scoring_agent.py      # ✅ Claude API tool use, score 0-10 con reasoning
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py           # engine, SessionLocal, Base, get_db()
│   │   ├── listing.py            # modelo Listing con UniqueConstraint
│   │   └── repository.py        # save_listing() con dedup + IntegrityError
│   │
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── base_scraper.py       # ABC BaseScraper + RawListing dataclass
│   │   ├── idealista_scraper.py  # ⚠️ bloqueado por CAPTCHA, pendiente proxies
│   │   ├── wallapop_scraper.py   # ✅ API interna interceptada con Playwright
│   │   └── run_scrapers.py       # orquestador: scrapers + QA agent
│   │
│   ├── api/                      # vacío, para FastAPI (semana 2)
│   └── notifications/            # vacío, para Twilio + SendGrid (semana 2)
│
├── frontend/                     # vacío, para React (semana 2)
└── .github/
    └── workflows/                # vacío, para CI/CD (semana 2)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ESTADO DE LA DB
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tabla: listings (migración aplicada)
Columnas: id, source, external_id, url, title, price, size_m2, rooms,
          neighborhood, district, lat, lon, description, score,
          scraped_at, created_at
Constraint: UNIQUE (source, external_id)
Datos actuales: ~33 pisos en venta reales (Lavapiés, Malasaña, Chueca, Palacio)
  - Con: precio, habitaciones, descripción completa, lat/lon, barrio
  - Sin: size_m2 (no disponible en search API de Wallapop), score_reasoning

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEPENDENCIAS INSTALADAS (pyproject.toml)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
playwright, beautifulsoup4, httpx
sqlalchemy, psycopg2-binary, alembic, python-dotenv
playwright-stealth
anthropic

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NOTAS TÉCNICAS IMPORTANTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- playwright-stealth v2.x usa: Stealth().apply_stealth_async(page)
  (NO stealth_async — eso es v1)
- Wallapop scraper usa intercepción de red (page.on("response", ...))
  para capturar la API interna /api/v3/search/section que devuelve JSON
  completo con precio, descripción, habitaciones, coordenadas
- El scraper NO captura size_m2 (solo está en la página individual de cada piso)
- Idealista tiene CAPTCHA agresivo, pendiente resolver con proxies
- QA agent filtra: alquileres, precio < 50k, precio > 2M, m² anómalos
- Scoring agent usa Claude tool use (function calling) para structured output
  garantizado — patrón clave para entrevistas AI Engineer
- Todos los métodos son async (Playwright + httpx)
- DB connection string en .env:
  DATABASE_URL=postgresql://franciscocrespo@localhost:5432/madrid_flip_hunter
- IMPORTANTE: ejecutar siempre con `poetry run python`, no `python` directamente
  (el sistema tiene Python 3.9, Poetry usa 3.11)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CÓMO EJECUTAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scraping + QA
poetry run python -m backend.scrapers.run_scrapers

# Scoring
poetry run python -m backend.agents.scoring_agent

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRÓXIMA SESIÓN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PASO 1 — Migración Alembic: guardar reasoning en DB (~15 min)
  - Añadir columnas: score_reasoning TEXT, score_green_flags TEXT, score_red_flags TEXT
  - Crear migración con alembic revision --autogenerate
  - Actualizar scoring_agent.py para persistir los tres campos
  - Concepto clave para portfolio: migraciones de schema en producción

PASO 2 — Dashboard básico: FastAPI + React + Leaflet (~resto de sesión)
  - FastAPI: endpoint GET /api/listings que devuelve listings con score
  - React: tabla de pisos ordenada por score
  - Leaflet.js: mapa de Madrid con pins por score (verde/amarillo/rojo)
  - Esto es lo que más impacta visualmente en entrevistas

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CÓMO ENSEÑARME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Un módulo por sesión, paso a paso
- Explicar el "por qué" de cada decisión técnica
- Si hay varias opciones, mostrarlas y ayudarme a elegir
- Code review de las partes clave al escribir código
- Relacionar cada concepto con entrevistas de AI Engineer
- Al final de cada sesión: resumen + próximo paso
- IMPORTANTE: ejecutar siempre con poetry run python
- IMPORTANTE: cuando se crea un archivo nuevo, hacerlo desde terminal
  con cat > archivo << 'EOF' para evitar errores de edición
