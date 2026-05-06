# backend/agents/reset_and_rescore.py
import asyncio
import logging
from backend.models.database import SessionLocal
from backend.models.listing import Listing
from backend.agents.scoring_agent import run_scoring_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def reset_scores():
    db = SessionLocal()
    try:
        updated = (
            db.query(Listing)
            .filter(Listing.is_active.is_(True))
            .update({"score": None, "score_reasoning": None,
                     "score_green_flags": None, "score_red_flags": None})
        )
        db.commit()
        logger.info("Reset score=NULL en %d listings activos", updated)
    finally:
        db.close()

if __name__ == "__main__":
    reset_scores()
    asyncio.run(run_scoring_agent())
