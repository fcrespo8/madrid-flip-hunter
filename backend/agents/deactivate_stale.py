from datetime import datetime, timedelta
from backend.models.database import SessionLocal
from backend.models.listing import Listing
import logging

logger = logging.getLogger(__name__)

STALE_DAYS = 30


def deactivate_stale() -> None:
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=STALE_DAYS)
        stale = db.query(Listing).filter(
            Listing.last_seen_at < cutoff,
            Listing.is_active.is_(True),
        ).all()
        for listing in stale:
            listing.is_active = False
        db.commit()
        logger.info("[deactivate_stale] %d listings marcados como inactivos", len(stale))
    finally:
        db.close()


if __name__ == "__main__":
    deactivate_stale()
