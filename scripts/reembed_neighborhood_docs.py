"""Re-embed all neighborhood docs with the current embedding model.

Useful when the embedding model or preprocessing changes — re-runs the
vector generation without regenerating the textual content (no Claude API calls).
"""
from __future__ import annotations

import re

from backend.models.database import SessionLocal
from backend.models.neighborhood_doc import NeighborhoodDoc
from backend.rag.embeddings import embed_batch


def clean_for_embedding(text: str) -> str:
    """Strip markdown headers and formatting that adds noise to embeddings."""
    # Remove bold markdown headers like **FICHA INTERNA — ...**
    text = re.sub(r"\*\*[^\n*]+\*\*", "", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def reembed() -> None:
    session = SessionLocal()
    try:
        docs = session.query(NeighborhoodDoc).all()
        if not docs:
            print("No docs to re-embed.")
            return

        print(f"Re-embedding {len(docs)} docs...")
        cleaned = [clean_for_embedding(d.content) for d in docs]
        vectors = embed_batch(cleaned)

        for doc, vector in zip(docs, vectors):
            doc.embedding = vector

        session.commit()
        print(f"✅ Done. {len(docs)} docs re-embedded.")
    except Exception as e:
        session.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    reembed()
