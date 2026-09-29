"""Punto de entrada del scoring: run_scoring() y `python -m backend.scoring.runner`."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from backend.models.listing import Listing
from backend.models.repository import pending_listings_query
from backend.observability.tracing import get_langfuse
from backend.scoring.graph import build_scoring_graph

logger = logging.getLogger(__name__)

MAX_SCORE_ATTEMPTS = 3


@dataclass
class ScoringSummary:
    total: int = 0
    scored_ids: list[int] = field(default_factory=list)   # guardados con éxito
    failed: list[tuple[int, str]] = field(default_factory=list)  # (listing_id, error)


def _record_failure(db: Session, listing: Listing, error: str) -> None:
    """Tras un intento fallido: al llegar a MAX_SCORE_ATTEMPTS, marca 'failed'.
    Antes de eso queda en 'pending' y la próxima corrida lo reintenta."""
    if (listing.score_attempts or 0) < MAX_SCORE_ATTEMPTS:
        return
    try:
        listing.score_status = "failed"
        listing.score_status_reason = error[:500]
        db.commit()
    except Exception as e:
        logger.error("DB error marking listing %s as failed: %s", listing.id, e)
        db.rollback()


async def run_scoring(
    listings: list[Listing] | None = None,
    db: Session | None = None,
    *,
    client=None,
    retriever: Callable | None = None,
    limit: int | None = None,
) -> ScoringSummary:
    """Puntúa listings uno por uno con el grafo. No notifica.

    - Sin `listings`: busca los pendientes (ver pending_listings_query), hasta `limit`.
    - Cada intento suma score_attempts (con commit antes de llamar a Claude, así
      cuenta aunque el proceso se caiga). Éxito → 'llm'; al fallar el intento
      MAX_SCORE_ATTEMPTS → 'failed'.
    - Sin `db`: abre y cierra su propia sesión. Si pasás `listings`, pasá también
      la `db` a la que pertenecen, para que el commit los incluya.
    """
    if listings is not None and db is None:
        raise ValueError("run_scoring: si pasás listings, pasá también su sesión db")

    own_session = db is None
    if own_session:
        from backend.models.database import SessionLocal

        db = SessionLocal()

    summary = ScoringSummary()
    langfuse = get_langfuse()
    graph = build_scoring_graph(client=client, retriever=retriever)

    try:
        if listings is None:
            query = pending_listings_query(db)
            if limit is not None:
                query = query.limit(limit)
            listings = query.all()
        summary.total = len(listings)
        logger.info("%d listings para scoring", summary.total)

        for listing in listings:
            listing_id = listing.id
            logger.info("Scoring: %s...", (listing.title or "")[:60])
            lf_span = None
            if langfuse:
                lf_span = langfuse.start_observation(
                    name="score_listing",
                    as_type="span",
                    metadata={
                        "listing_id": listing_id,
                        "neighborhood": listing.neighborhood,
                        "district": listing.district,
                        "source": listing.source,
                        "price": listing.price,
                        "size_m2": listing.size_m2,
                        "pipeline": "langgraph",
                    },
                )
            try:
                listing.score_attempts = (listing.score_attempts or 0) + 1
                db.commit()
                final = await graph.ainvoke({"listing": listing, "db": db, "_lf_span": lf_span})
                error = final.get("error")
            except Exception as e:  # red de seguridad: un listing no frena el lote
                logger.error("Graph failed for listing %s: %s", listing_id, e)
                db.rollback()
                final, error = {}, f"{type(e).__name__}: {e}"

            if final.get("saved"):
                summary.scored_ids.append(listing_id)
                if lf_span:
                    result = final["result"]
                    lf_span.update(output={"score": result.score, "reasoning": result.reasoning})
            else:
                summary.failed.append((listing_id, error or "unknown"))
                _record_failure(db, listing, error or "unknown")
                if lf_span:
                    lf_span.update(metadata={"error": error})
            if lf_span:
                lf_span.end()
    finally:
        if langfuse:
            langfuse.flush()
        if own_session:
            db.close()

    logger.info("Scoring terminado: %d guardados, %d fallidos", len(summary.scored_ids), len(summary.failed))
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_scoring())
