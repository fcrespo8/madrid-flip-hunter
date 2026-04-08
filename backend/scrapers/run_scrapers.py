import asyncio
from backend.models.database import SessionLocal
from backend.models.repository import save_listing
from backend.scrapers.wallapop_scraper import WallapopScraper
from backend.agents.qa_agent import QAAgent


async def run_all():
    scrapers = [WallapopScraper()]

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

        # QA agent: elimina alquileres y anomalías
        qa = QAAgent()
        qa.run(db)

    finally:
        db.close()

    print(f"\n✅ Total: {total_new} nuevos, {total_dup} duplicados.")


if __name__ == "__main__":
    asyncio.run(run_all())
