from anthropic import AsyncAnthropic
from sqlalchemy.orm import Session
from backend.models.listing import Listing
from backend.models.database import SessionLocal

SCORE_TOOL = {
    "name": "score_listing",
    "description": "Evalúa una oportunidad de flipping inmobiliario en Madrid",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "description": "Score de 0 a 10. 0=horrible, 10=oportunidad excepcional",
            },
            "reasoning": {
                "type": "string",
                "description": "2-3 frases explicando el score. Sé concreto: precio/m², barrio, estado.",
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Señales negativas detectadas",
            },
            "green_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Señales positivas detectadas",
            },
        },
        "required": ["score", "reasoning", "red_flags", "green_flags"],
    },
}

SYSTEM_PROMPT = """Eres un experto en inversión inmobiliaria en Madrid especializado en flipping.
Tu objetivo es identificar pisos con potencial de revalorización rápida (comprar, reformar, vender).

Criterios de scoring:
- PRECIO/M²: En Madrid centro el precio medio es ~5.000€/m². Por debajo de 3.500€/m² es muy interesante.
- BARRIO: Malasaña, Chueca, Lavapiés, Chamberí > Vallecas, Carabanchel (mayor margen pero más riesgo)
- DESCRIPCIÓN: "a reformar", "reforma", "herencia", "urgente", "necesita actualización" son señales muy positivas
- TAMAÑO: 40-95m² es el sweet spot para flipping en Madrid (liquidez alta)
- PRECIO ABSOLUTO: Tickets entre 150k-350k son los más accesibles para inversores típicos

Sé crítico y preciso. Un score alto (8+) debe ser genuinamente excepcional."""


async def score_listing(listing: Listing) -> dict:
    client = AsyncAnthropic()

    price_per_m2 = (
        f"{listing.price / listing.size_m2:.0f}€/m²"
        if listing.size_m2 and listing.size_m2 > 0
        else "desconocido"
    )

    listing_context = f"""
PISO A EVALUAR:
- Título: {listing.title}
- Precio: {listing.price:,.0f}€
- Tamaño: {listing.size_m2 or 'desconocido'}m²
- Precio/m²: {price_per_m2}
- Habitaciones: {listing.rooms or 'desconocido'}
- Barrio: {listing.neighborhood or 'desconocido'}
- Distrito: {listing.district or 'desconocido'}
- Fuente: {listing.source}
- Descripción: {listing.description or 'Sin descripción'}
""".strip()

    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[SCORE_TOOL],
        tool_choice={"type": "auto"},
        messages=[
            {
                "role": "user",
                "content": f"Evalúa esta oportunidad de flipping:\n\n{listing_context}",
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "score_listing":
            return block.input

    raise ValueError(f"Claude no devolvió tool_use para listing {listing.id}")


async def run_scoring_agent():
    db: Session = SessionLocal()
    try:
        pending = db.query(Listing).filter(Listing.score == None).all()
        print(f"🔍 {len(pending)} listings pendientes de scoring")

        for listing in pending:
            print(f"\n📊 Scoring: {listing.title[:60]}...")
            try:
                result = await score_listing(listing)
                listing.score = result["score"]
                db.commit()

                print(f"   ✅ Score: {result['score']}/10")
                print(f"   💬 {result['reasoning']}")
                if result["green_flags"]:
                    print(f"   ✅ Green: {', '.join(result['green_flags'])}")
                if result["red_flags"]:
                    print(f"   ⚠️  Red:   {', '.join(result['red_flags'])}")

            except Exception as e:
                print(f"   ❌ Error scoring listing {listing.id}: {e}")
                db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_scoring_agent())
