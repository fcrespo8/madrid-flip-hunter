from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .listing import Listing
from backend.scrapers.base_scraper import RawListing


def save_listing(db: Session, raw: RawListing) -> tuple[Listing, bool]:
    """
    Guarda un RawListing en la DB.
    Retorna (listing, created) — created=False si ya existía.
    """
    existing = db.query(Listing).filter_by(
        source=raw.source,
        external_id=raw.external_id
    ).first()

    if existing:
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
