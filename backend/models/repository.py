import logging
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .listing import Listing
from .scrape_run import ScrapeRun
from backend.scrapers.base_scraper import RawListing, ScrapeResult

logger = logging.getLogger(__name__)


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


def record_scrape_run(db: Session, source: str, result: ScrapeResult, new_count: int) -> None:
    """Guarda cómo fue el barrido de una fuente. Es contabilidad: si falla (p. ej. la
    migración todavía no se aplicó) se loguea y el scraping sigue."""
    try:
        db.add(ScrapeRun(
            source=source,
            seen_count=len(result.listings),
            new_count=new_count,
            complete=result.complete,
            total_reported=result.total_reported,
            incomplete_reason=result.incomplete_reason,
            error=(result.error or "")[:1000] or None,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("No se pudo registrar el barrido de %s en scrape_runs: %s", source, e)
