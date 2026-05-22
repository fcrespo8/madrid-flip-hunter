"""Retrieval function for the RAG knowledge base.

Hybrid search: filter by distrito (structured metadata) first,
then rank by cosine similarity inside that filter. Falls back to
global vector search if the distrito has no docs.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models.neighborhood_doc import NeighborhoodDoc
from backend.rag.embeddings import embed_text


def retrieve_context(
    session: Session,
    barrio: str,
    distrito: str,
    top_k: int = 2,
) -> list[NeighborhoodDoc]:
    """Return the top_k neighborhood docs most relevant to the location.

    Strategy: filter by distrito first (structured), then rank by
    cosine similarity to a query built from the barrio name.
    If no docs exist for that distrito, fall back to global vector search.
    """
    query_vector = embed_text(
    f"Barrio {barrio} en el distrito {distrito} de Madrid. "
    f"Características del barrio, gentrificación, perfil de comprador, potencial de inversión inmobiliaria."
    )

    # 1. Try filtered search first (within the same distrito)
    results = (
        session.query(NeighborhoodDoc)
        .filter(NeighborhoodDoc.distrito == distrito)
        .order_by(NeighborhoodDoc.embedding.cosine_distance(query_vector))
        .limit(top_k)
        .all()
    )

    if results:
        return results

    # 2. Fallback: no docs in that distrito, search globally
    return (
        session.query(NeighborhoodDoc)
        .order_by(NeighborhoodDoc.embedding.cosine_distance(query_vector))
        .limit(top_k)
        .all()
    )
