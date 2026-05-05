from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.models.database import SessionLocal
from backend.models.listing import Listing
from backend.scrapers.run_scrapers import run_all
from backend.agents.market_prices import get_market_price
import os

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app):
    scheduler.add_job(run_all, "cron", hour=7, minute=0)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Madrid Flip Hunter", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/listings")
def get_listings():
    db: Session = SessionLocal()
    try:
        listings = (
            db.query(Listing)
            .filter(Listing.score.is_not(None))
            .filter(Listing.is_active.is_(True))
            .order_by(Listing.score.desc())
            .all()
        )
        result = []
        for listing in listings:
            market_price = get_market_price(listing.neighborhood, listing.district)
            ppm2 = listing.price / listing.size_m2 if listing.price and listing.size_m2 else None
            vs_market_pct = (
                round((ppm2 - market_price) / market_price * 100, 1)
                if ppm2 and market_price
                else None
            )
            result.append({
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
                "score_reasoning": listing.score_reasoning,
                "score_green_flags": listing.score_green_flags,
                "score_red_flags": listing.score_red_flags,
                "url": listing.url,
                "source": listing.source,
                "market_price_m2": market_price,
                "vs_market_pct": vs_market_pct,
            })
        return result
    finally:
        db.close()

# Servir el frontend
frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
