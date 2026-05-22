"""Seed script for neighborhood_docs RAG knowledge base.

Generates ~30 qualitative descriptions of Madrid neighborhoods using
the Claude API, embeds them with sentence-transformers, and upserts
them into the neighborhood_docs table.

Run with: poetry run python scripts/seed_neighborhood_docs.py
"""
from __future__ import annotations

import os
import sys

from anthropic import Anthropic
from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert

from backend.models.database import SessionLocal
from backend.models.neighborhood_doc import NeighborhoodDoc
from backend.rag.embeddings import embed_batch


load_dotenv()


# 30 neighborhoods ordered by listing volume in Idealista Madrid
NEIGHBORHOODS: list[tuple[str, str]] = [
    # Centro (5)
    ("Malasaña-Universidad", "Centro"),
    ("Chueca-Justicia", "Centro"),
    ("Lavapiés-Embajadores", "Centro"),
    ("Sol", "Centro"),
    ("Huertas-Cortes", "Centro"),
    # Salamanca (5)
    ("Recoletos", "Barrio de Salamanca"),
    ("Goya", "Barrio de Salamanca"),
    ("Lista", "Barrio de Salamanca"),
    ("Castellana", "Barrio de Salamanca"),
    ("Guindalera", "Barrio de Salamanca"),
    # Chamberí (4)
    ("Almagro", "Chamberí"),
    ("Trafalgar", "Chamberí"),
    ("Arapiles", "Chamberí"),
    ("Nuevos Ministerios-Ríos Rosas", "Chamberí"),
    # Retiro (3)
    ("Ibiza", "Retiro"),
    ("Pacífico", "Retiro"),
    ("Jerónimos", "Retiro"),
    # Tetuán (3)
    ("Cuatro Caminos", "Tetuán"),
    ("Bellas Vistas", "Tetuán"),
    ("Berruguete", "Tetuán"),
    # Chamartín (3)
    ("Prosperidad", "Chamartín"),
    ("El Viso", "Chamartín"),
    ("Bernabéu-Hispanoamérica", "Chamartín"),
    # Arganzuela (2)
    ("Delicias", "Arganzuela"),
    ("Palos de la Frontera", "Arganzuela"),
    # Emergentes (5)
    ("Pueblo Nuevo", "Ciudad Lineal"),
    ("Argüelles", "Moncloa"),
    ("Puerta del Ángel", "Latina"),
    ("Numancia", "Puente de Vallecas"),
    ("Comillas", "Carabanchel"),
]


SYSTEM_PROMPT = (
    "Eres Carlos Martínez, inversor inmobiliario experto en Madrid con 20 años "
    "de experiencia en operaciones de flipping (compra-reforma-venta). Escribes "
    "fichas internas para un sistema de evaluación de oportunidades."
)


USER_PROMPT_TEMPLATE = """Escribe una ficha sobre el barrio {barrio} (distrito {distrito}) de Madrid, cubriendo en un solo párrafo continuo (sin títulos, sin viñetas) estos cinco puntos:

1. Estado de gentrificación (en alza, consolidada, estable, en declive) y rango actual de precios €/m².
2. Perfil del comprador típico (edad, perfil profesional, motivación de compra).
3. Potencial de flip (¿funciona? ¿por qué? margen aproximado).
4. Tipo de reforma que mejor retorna en este barrio (estilo, materiales, target precio venta €/m²).
5. Red flags específicas del barrio (ruido, turismo, regulación, problemas estructurales típicos).

Sé específico, concreto y usa números reales del mercado. Usa vocabulario de inversor (rental yield, VUT, reforma integral, etc.). 4-6 frases en total."""


def generate_doc(client: Anthropic, barrio: str, distrito: str) -> str:
    """Call Claude API to generate a neighborhood description."""
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(barrio=barrio, distrito=distrito),
            }
        ],
    )
    return message.content[0].text.strip()


def seed() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    client = Anthropic()

    # 1. Generate all docs sequentially
    print(f"Generating {len(NEIGHBORHOODS)} neighborhood docs with Claude API...")
    docs: list[dict[str, str]] = []
    for i, (barrio, distrito) in enumerate(NEIGHBORHOODS, start=1):
        print(f"  [{i:2d}/{len(NEIGHBORHOODS)}] {barrio} ({distrito})...", flush=True)
        try:
            content = generate_doc(client, barrio, distrito)
            docs.append({"barrio": barrio, "distrito": distrito, "content": content})
        except Exception as e:
            print(f"    FAILED: {e}", file=sys.stderr)
            continue

    if not docs:
        print("ERROR: No docs generated", file=sys.stderr)
        sys.exit(1)

    # 2. Embed all in one batch (single forward pass)
    print(f"\nEmbedding {len(docs)} docs with sentence-transformers...")
    contents = [d["content"] for d in docs]
    vectors = embed_batch(contents)

    # 3. Upsert into database using the unique constraint
    print("\nUpserting into neighborhood_docs...")
    session = SessionLocal()
    inserted = 0
    try:
        for doc, vector in zip(docs, vectors):
            stmt = insert(NeighborhoodDoc).values(
                barrio=doc["barrio"],
                distrito=doc["distrito"],
                content=doc["content"],
                embedding=vector,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_neighborhood_docs_barrio_distrito",
                set_={
                    "content": stmt.excluded.content,
                    "embedding": stmt.excluded.embedding,
                },
            )
            session.execute(stmt)
            inserted += 1
        session.commit()
        print(f"\n✅ Done. {inserted} docs upserted into neighborhood_docs.")
    except Exception as e:
        session.rollback()
        print(f"ERROR during upsert: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    seed()
