"""Embedding utility using sentence-transformers.

Loads the model once (lazy singleton) and exposes a simple
function to convert text into a 384-dimensional vector.
"""
from __future__ import annotations

from functools import lru_cache
from sentence_transformers import SentenceTransformer


MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """Load the embedding model once and cache it in memory."""
    return SentenceTransformer(MODEL_NAME)


def embed_text(text: str) -> list[float]:
    """Convert a single string into a 384-dim embedding vector."""
    if not text or not text.strip():
        raise ValueError("Cannot embed empty text")
    model = _get_model()
    vector = model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
    return vector.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Convert a list of strings into a list of 384-dim vectors.

    More efficient than calling embed_text in a loop because the model
    processes everything in a single forward pass.
    """
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return [v.tolist() for v in vectors]
