# backend/agents/reset_and_rescore.py
"""Resetea el score de listings activos y los vuelve a puntuar con Claude.

Por defecto es dry-run: solo muestra cuántos listings se puntuarían, sin tocar
la DB ni llamar a Claude. Para ejecutar de verdad: --confirm.

    poetry run python -m backend.agents.reset_and_rescore             # dry-run
    poetry run python -m backend.agents.reset_and_rescore --limit 5   # dry-run de 5
    poetry run python -m backend.agents.reset_and_rescore --confirm --limit 5
"""
import argparse
import asyncio
import logging

from backend.models.listing import Listing
from backend.scoring.runner import run_scoring

logger = logging.getLogger(__name__)

PREVIEW_ROWS = 10


def select_targets(db, limit: int | None = None) -> list[Listing]:
    """Listings activos, por id. Con limit, solo esos N se resetean y puntúan."""
    query = db.query(Listing).filter(Listing.is_active.is_(True)).order_by(Listing.id)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def reset_scores(db, listings: list[Listing]) -> None:
    for listing in listings:
        listing.score = None
        listing.score_reasoning = None
        listing.score_green_flags = None
        listing.score_red_flags = None
        listing.scored_at = None
    db.commit()


async def main(confirm: bool, limit: int | None, db=None, client=None) -> int:
    """Devuelve la cantidad de listings seleccionados. `db`/`client` se inyectan en tests."""
    own_session = db is None
    if own_session:
        from backend.models.database import SessionLocal

        db = SessionLocal()
    try:
        targets = select_targets(db, limit)

        if not confirm:
            print(f"[dry-run] Se resetearían y puntuarían {len(targets)} listings activos con Claude.")
            for listing in targets[:PREVIEW_ROWS]:
                print(f"  - #{listing.id} score={listing.score} {(listing.title or '')[:60]}")
            if len(targets) > PREVIEW_ROWS:
                print(f"  ... y {len(targets) - PREVIEW_ROWS} más")
            print("Nada se modificó. Para ejecutar: --confirm")
            return len(targets)

        reset_scores(db, targets)
        logger.info("Reset score=NULL en %d listings activos", len(targets))
        summary = await run_scoring(targets, db, client=client)
        print(f"Rescore: {len(summary.scored_ids)} guardados, {len(summary.failed)} fallidos.")
        for listing_id, error in summary.failed:
            print(f"  ✗ #{listing_id}: {error}")
        return len(targets)
    finally:
        if own_session:
            db.close()


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("--limit debe ser >= 1")
    return n


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true", help="ejecutar de verdad (resetea y llama a Claude)")
    parser.add_argument("--limit", type=_positive_int, default=None, help="solo los primeros N listings activos")
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    asyncio.run(main(confirm=args.confirm, limit=args.limit))
