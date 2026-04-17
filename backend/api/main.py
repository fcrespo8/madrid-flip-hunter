from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from backend.models.database import SessionLocal
from backend.models.listing import Listing
import os

app = FastAPI(title="Madrid Flip Hunter")

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
            .order_by(Listing.score.desc())
            .all()
        )
        return [
            {
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
            }
            for listing in listings
        ]
    finally:
        db.close()

# Servir el frontend
frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
