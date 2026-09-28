"""Contexto cualitativo del barrio desde la base RAG."""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.orm import Session

from backend.models.listing import Listing

logger = logging.getLogger(__name__)

TOP_K = 2


def _default_retriever(db: Session, barrio: str, distrito: str, top_k: int):
    # Import perezoso: backend.rag.retrieval carga sentence-transformers.
    from backend.rag.retrieval import retrieve_context

    return retrieve_context(db, barrio=barrio, distrito=distrito, top_k=top_k)


def format_rag_context(docs) -> str:
    if not docs:
        return ""
    blocks = "\n\n".join(f"[{d.barrio} - {d.distrito}]\n{d.content}" for d in docs)
    return f"\n\nCONTEXTO CUALITATIVO DEL BARRIO (de tu base de conocimiento):\n{blocks}"


def get_rag_context(
    db: Session,
    listing: Listing,
    retriever: Callable | None = None,
) -> tuple[str, int]:
    """Devuelve (contexto formateado, cantidad de docs). Nunca lanza.

    Si la consulta falla hace rollback de la sesión: si no, la sesión compartida
    queda en estado inválido y el commit posterior del score falla.
    """
    if not (listing.neighborhood and listing.district):
        return "", 0

    retriever = retriever or _default_retriever
    try:
        docs = retriever(db, listing.neighborhood, listing.district, TOP_K)
    except Exception as e:
        logger.warning("RAG retrieval failed for listing %s: %s", listing.id, e)
        db.rollback()
        return "", 0
    return format_rag_context(docs), len(docs)
