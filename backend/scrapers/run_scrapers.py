import asyncio
import logging
from datetime import datetime
from backend.models.database import SessionLocal
from backend.models.listing import Listing
from backend.models.repository import save_listing
from backend.scrapers.wallapop_scraper import WallapopScraper
from backend.scrapers.donpiso_scraper import DonpisoScraper
from backend.scrapers.remax_scraper import RemaxScraper        # ← nuevo
from backend.scrapers.redpiso_scraper import RedpisoScraper    # ← nuevo
from backend.scrapers.tecnocasa_scraper import TecnocasaScraper
from backend.agents.qa_agent import QAAgent
from backend.agents.enrich_location import enrich_locations
from backend.agents.deactivate_stale import deactivate_stale
from backend.agents.notifier import send_whatsapp_alerts
from backend.agents.pre_scorer import pre_score
from backend.scoring.runner import run_scoring

logger = logging.getLogger(__name__)

NOTIFY_MIN_SCORE = 7.5


async def notify_scored(candidates: list[Listing], scored_ids: list[int], db) -> None:
    """Avisa por WhatsApp solo los recién puntuados en esta corrida con score alto."""
    scored = set(scored_ids)
    for listing in candidates:
        if listing.id not in scored or listing.score is None:
            continue
        if listing.score < NOTIFY_MIN_SCORE or listing.notified_at is not None:
            continue
        try:
            await send_whatsapp_alerts([listing])
            listing.notified_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            logger.error("Notification error for listing %s: %s", listing.id, e)
            db.rollback()


async def run_all():
    scrapers = [
        WallapopScraper(),
        DonpisoScraper(),
        RemaxScraper(),     # ← nuevo
        RedpisoScraper(),   # ← nuevo
        TecnocasaScraper(),
    ]

    db = SessionLocal()
    total_new, total_dup = 0, 0

    try:
        for scraper in scrapers:
            listings = await scraper.run()
            new_count, dup_count = 0, 0
            for raw in listings:
                _, created = save_listing(db, raw)
                if created:
                    new_count += 1
                else:
                    dup_count += 1
            print(f"[{scraper.source_name}] {new_count} nuevos, {dup_count} duplicados.")
            total_new += new_count
            total_dup += dup_count

        qa = QAAgent()
        qa.run(db)
        enrich_locations()
        deactivate_stale()

        # Pre-scoring: filtra candidatos para Claude sin coste de API
        unscored = db.query(Listing).filter(Listing.score.is_(None)).all()
        candidatos_claude = []
        for listing in unscored:
            ps = pre_score(listing)
            if ps is not None and ps >= 7.0:
                candidatos_claude.append(listing)
            else:
                listing.score = ps
                listing.score_reasoning = "Score automático: precio vs mercado"
        db.commit()
        logger.info("%d candidatos para scoring Claude (pre-score >= 7.0)", len(candidatos_claude))

        summary = await run_scoring(candidatos_claude, db)
        await notify_scored(candidatos_claude, summary.scored_ids, db)

    finally:
        db.close()

    print(f"\n✅ Total: {total_new} nuevos, {total_dup} duplicados.")


if __name__ == "__main__":
    asyncio.run(run_all())
