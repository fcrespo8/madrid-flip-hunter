from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .listing import Listing
from backend.scrapers.base_scraper import RawListing


def save_listing(db: Session, raw: RawListing) -> tuple[Listing, bool]:
    """
    Guarda un RawListing en la DB.
    Retorna (listing, created) — created=False si ya existía.
    Si ya existía, actualiza last_seen_at y lo reactiva: si el scraper lo vio,
    sigue publicado (aunque deactivate_stale o un admin lo hayan desactivado).
    """
    existing = db.query(Listing).filter_by(
        source=raw.source,
        external_id=raw.external_id
    ).first()

    if existing:
        existing.last_seen_at = datetime.utcnow()
        existing.is_active = True
        db.commit()
        return existing, False

    listing = Listing(
        source=raw.source,
        external_id=raw.external_id,
        url=raw.url,
        title=raw.title,
        price=raw.price,
        size_m2=raw.size_m2,
        rooms=raw.rooms,
        neighborhood=raw.neighborhood,
        district=raw.district,
        lat=raw.lat,
        lon=raw.lon,
        description=raw.description,
    )

    try:
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return listing, True
    except IntegrityError:
        db.rollback()
        return db.query(Listing).filter_by(
            source=raw.source,
            external_id=raw.external_id
        ).first(), False


def pending_listings_query(db: Session):
    """Listings que el pipeline tiene que procesar: pendientes, no rechazados por QA
    y activos, del más viejo al más nuevo. Usa el índice parcial ix_listings_pending."""
    return (
        db.query(Listing)
        .filter(
            Listing.score_status == "pending",
            Listing.qa_rejected.is_(False),
            Listing.is_active.is_(True),
        )
        .order_by(Listing.id)
    )
