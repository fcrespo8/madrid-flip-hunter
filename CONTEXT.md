## Madrid Flip Hunter - Context para AI

Sistema multi-agente en Python para flipping inmobiliario en Madrid.
Portfolio para entrevistas AI Engineer en ~2 semanas.

### Stack completo
Python 3.11, PostgreSQL, Playwright, BeautifulSoup, FastAPI, React, 
Leaflet.js, Claude API, APScheduler, Docker, GitHub Actions, Railway,
Twilio (WhatsApp), SendGrid (Email)

### Módulos
- backend/scrapers/ → Idealista, Fotocasa, Habitaclia, Milanuncios, Wallapop
- backend/agents/  → Scoring con Claude API (0-10)
- backend/models/  → Listing, Opportunity, PriceHistory (SQLAlchemy)
- backend/api/     → FastAPI
- backend/notifications/ → Twilio + SendGrid
- frontend/        → React + Leaflet.js

### Repo
https://github.com/fcrespo8/madrid-flip-hunter

### Sesiones completadas
**Sesión 1:**
- Git + Poetry 2.3.2 configurados
- Estructura de carpetas creada
- Dependencias instaladas: playwright, beautifulsoup4, httpx
- Chromium descargado para Playwright
- Primer commit en GitHub

### Arquitectura multi-agente
4 agentes en pipeline secuencial:
1. Scraper agent → Playwright + BeautifulSoup + APScheduler
2. QA agent → dedup, validación, anomalías
3. Enrichment agent → precio/m², zona, transporte, historial
4. Scoring agent → Claude API, score 0-10 con reasoning

Outputs: Notification agent (Twilio + SendGrid) + Dashboard (FastAPI + React + Leaflet)
Storage: PostgreSQL con SQLAlchemy (Listing, Opportunity, PriceHistory)

**Sesión 2:**
- BaseScraper + IdealistaScraper creados (backend/scrapers/)
- database.py con SQLAlchemy 2.0 + SessionLocal
- Listing model con UniqueConstraint (source, external_id)
- Alembic inicializado, migración aplicada
- Tabla listings creada en PostgreSQL local
- Próximo: repository layer + conectar scraper con DB

**Sesión 3:**
- Repository layer creado (save_listing con dedup)
- WallapopScraper funcionando — 40 listings reales en DB
- playwright-stealth integrado (API v2: Stealth().apply_stealth_async)
- Idealista bloqueado por CAPTCHA — pendiente con proxies
- run_scrapers.py orquesta todos los scrapers
- Próximo: QA agent (dedup, validación, detección anomalías)
