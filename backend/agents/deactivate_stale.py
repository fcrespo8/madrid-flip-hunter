from datetime import datetime, timedelta
from typing import Iterable
from backend.models.database import SessionLocal
from backend.models.listing import Listing
import argparse
import logging

logger = logging.getLogger(__name__)

STALE_DAYS = 30


def stale_listings_query(db, sources: list[str], cutoff: datetime):
    return db.query(Listing).filter(
        Listing.source.in_(sources),
        Listing.last_seen_at < cutoff,
        Listing.is_active.is_(True),
    )


def deactivate_stale(sources: Iterable[str]) -> int:
    """Marca inactivos los listings no vistos en STALE_DAYS, **solo de `sources`**:
    las fuentes que se scrapearon bien en esta corrida. Si una fuente no se scrapeó,
    no hay forma de saber si sus listings siguen publicados, así que no se tocan.
    Sin fuentes no desactiva nada. Devuelve cuántos desactivó."""
    sources = sorted(set(sources))
    if not sources:
        logger.warning("[deactivate_stale] ninguna fuente se scrapeó bien: no se desactiva nada")
        return 0
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=STALE_DAYS)
        stale = stale_listings_query(db, sources, cutoff).all()
        for listing in stale:
            listing.is_active = False
        db.commit()
        logger.info("[deactivate_stale] %d listings marcados como inactivos (fuentes: %s)", len(stale), sources)
        return len(stale)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Desactiva listings no vistos en STALE_DAYS de las fuentes dadas.")
    parser.add_argument("sources", nargs="+", help="fuentes a revisar, p. ej. wallapop donpiso")
    logging.basicConfig(level=logging.INFO)
    print(deactivate_stale(parser.parse_args().sources))
