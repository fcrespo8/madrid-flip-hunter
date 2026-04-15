from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
            .filter(Listing.score != None)
            .order_by(Listing.score.desc())
            .all()
        )
        return [
            {
                "id": l.id,
                "title": l.title,
                "price": l.price,
                "size_m2": l.size_m2,
                "rooms": l.rooms,
                "neighborhood": l.neighborhood,
                "district": l.district,
                "lat": l.lat,
                "lon": l.lon,
                "score": l.score,
                "score_reasoning": l.score_reasoning,
                "score_green_flags": l.score_green_flags,
                "score_red_flags": l.score_red_flags,
                "url": l.url,
            }
            for l in listings
        ]
    finally:
        db.close()

# Servir el frontend
frontend_path = os.path.join(os.path.dirname(__file__), "../../frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
