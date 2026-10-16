import os
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_
from sqlalchemy.orm import Session
from backend.auth.dependencies import get_current_user, require_admin
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.models.database import SessionLocal
from backend.models.listing import Listing
from backend.scrapers.run_scrapers import run_all
from backend.agents.market_prices import get_market_price
from backend.auth.router import router as auth_router
from backend.api.operations import router as operations_router
from backend.api.expenses import router as expenses_router
from backend.api.partners import router as partners_router
from backend.api.persons import router as persons_router
from backend.api.investor import router as investor_router

scheduler = AsyncIOScheduler()


async def scheduled_pipeline():
    """Job diario: el grafo del pipeline. PIPELINE_MAX_LLM_CALLS (opcional) pone tope
    a las llamadas a Claude por corrida."""
    from backend.pipeline.graph import run_pipeline

    max_llm_calls = os.environ.get("PIPELINE_MAX_LLM_CALLS")
    await run_pipeline(max_llm_calls=int(max_llm_calls) if max_llm_calls else None)


def schedule_daily_pipeline(sched) -> None:
    """07:00, una corrida a la vez (max_instances=1) y sin ponerse al día con las
    perdidas (coalesce=True). Rollback sin deploy de código: SCHEDULER_PIPELINE=legacy
    vuelve a run_all."""
    job = run_all if os.environ.get("SCHEDULER_PIPELINE") == "legacy" else scheduled_pipeline
    sched.add_job(job, "cron", hour=7, minute=0, id="daily_pipeline", replace_existing=True,
                  max_instances=1, coalesce=True)


@asynccontextmanager
async def lifespan(app):
    if os.environ.get("ENABLE_SCHEDULER") == "true":
        schedule_daily_pipeline(scheduler)
        scheduler.start()
    yield
    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(title="Madrid Flip Hunter", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(operations_router)
app.include_router(expenses_router)
app.include_router(partners_router)
app.include_router(persons_router)
app.include_router(investor_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def listing_to_dict(listing: Listing) -> dict:
    market_price = get_market_price(listing.neighborhood, listing.district)
    ppm2 = listing.price / listing.size_m2 if listing.price and listing.size_m2 else None
    vs_market_pct = (
        round((ppm2 - market_price) / market_price * 100, 1)
        if ppm2 and market_price
        else None
    )
    # La nota de Claude es `score` solo si la puntuó Claude; si no, `score` es la del pre-scorer.
    claude_score = listing.score if listing.score_status == "llm" else None
    return {
        "id": listing.id,
        "title": listing.title,
        "price": listing.price,
        "size_m2": listing.size_m2,
        "rooms": listing.rooms,
        "neighborhood": listing.neighborhood,
        "district": listing.district,
        "lat": listing.lat,
        "lon": listing.lon,
        "score": listing.score,
        "pre_score": listing.pre_score,
        "discount_pct": round(listing.discount_pct, 1) if listing.discount_pct is not None else None,
        "market_zone": listing.market_zone,
        "claude_score": claude_score,
        "has_claude": claude_score is not None,
        "score_status": listing.score_status,
        "score_reasoning": listing.score_reasoning,
        "score_green_flags": listing.score_green_flags,
        "score_red_flags": listing.score_red_flags,
        "url": listing.url,
        "source": listing.source,
        "market_price_m2": market_price,
        "vs_market_pct": vs_market_pct,
        "last_seen_at": listing.last_seen_at.strftime("%Y-%m-%d") if listing.last_seen_at else None,
        "created_at": listing.created_at.strftime("%Y-%m-%d") if listing.created_at else None,
        "scored_at": listing.scored_at.strftime("%Y-%m-%d") if listing.scored_at else None,
    }


@app.get("/api/listings")
def get_listings(sort: str = "pre_score", _=Depends(get_current_user)):
    """Listings activos con pre-score o con score. `sort=pre_score` (default): pre-score descendente
    y, a igual pre-score, mayor descuento sobre el mercado primero. `sort=score`: la nota final."""
    if sort not in ("pre_score", "score"):
        raise HTTPException(status_code=422, detail="sort debe ser 'pre_score' o 'score'")
    db: Session = SessionLocal()
    try:
        if sort == "score":
            order = [Listing.score.desc().nulls_last(), Listing.pre_score.desc().nulls_last(),
                     Listing.discount_pct.desc().nulls_last()]
        else:
            order = [Listing.pre_score.desc().nulls_last(), Listing.discount_pct.desc().nulls_last(),
                     Listing.score.desc().nulls_last()]
        listings = (
            db.query(Listing)
            .filter(Listing.is_active.is_(True), Listing.qa_rejected.is_(False))
            .filter(or_(Listing.score.is_not(None), Listing.pre_score.is_not(None)))
            .order_by(*order, Listing.id)
            .all()
        )
        return [listing_to_dict(listing) for listing in listings]
    finally:
        db.close()

@app.patch("/api/listings/{listing_id}")
def patch_listing(listing_id: int, body: dict, _=Depends(require_admin)):
    db: Session = SessionLocal()
    try:
        listing = db.query(Listing).filter_by(id=listing_id).first()
        if not listing:
            raise HTTPException(status_code=404, detail="Listing not found")
        if "is_active" in body:
            listing.is_active = bool(body["is_active"])
        db.commit()
        return {"id": listing_id, "is_active": listing.is_active}
    finally:
        db.close()


# Servir el frontend
frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
