import asyncio
from backend.models.database import SessionLocal
from backend.models.repository import save_listing
from backend.scrapers.idealista_scraper import IdealistaScraper


async def run_all():
    scraper = IdealistaScraper()
    listings = await scraper.run()

    db = SessionLocal()
    new_count, dup_count = 0, 0

    try:
        for raw in listings:
            _, created = save_listing(db, raw)
            if created:
                new_count += 1
            else:
                dup_count += 1
    finally:
        db.close()

    print(f"\n✅ Resultado: {new_count} nuevos, {dup_count} duplicados.")


if __name__ == "__main__":
    asyncio.run(run_all())
