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

SYSTEM_PROMPT = """Eres Carlos Martínez, inversor inmobiliario con 15 años de experiencia en flipping en Madrid. Has hecho más de 80 operaciones. Piensas en euros, márgenes y velocidad de venta.

TU PROCESO MENTAL:
1. ¿Hay margen? = precio compra + reforma + costes < precio venta estimado
2. ¿Hay urgencia del vendedor? = señales de precio negociable
3. ¿Hay demanda de salida? = ¿se venderá rápido una vez reformado?

CÓMO ESTIMAS SIN M²:
Si no hay m², lo estimas por habitaciones y precio:
- 1 hab en Madrid centro → estima 35-45m²
- 2 hab → estima 50-65m²
- 3 hab → estima 70-90m²
Luego calculas precio/m² estimado. Un inversor nunca se paraliza por falta de datos, estima.

CRITERIOS DE SCORING REALES:

PRECIO/M² (criterio más importante):
- <3.000€/m²: excepcional, score +3
- 3.000-4.000€/m²: muy interesante, score +2
- 4.000-5.000€/m²: mercado, neutro
- >5.500€/m²: caro para flipper, score -2
- >7.000€/m²: descarta directamente

SEÑALES DE VENDEDOR MOTIVADO (score +1 a +2 cada una):
- "herencia", "urgente", "liquidar", "necesita reforma", "a reformar"
- "propietario vende", "particular" (sin agencia)
- Tiempo largo en mercado
- Descripción escueta o poco profesional (dueño, no agencia)

POTENCIAL DE REFORMA (score +1 a +2):
- "a reformar", "para reformar", "habitabilidad" → margen de 20-40% típico post-reforma
- Baño/cocina original → reforma de 20-30k€ que añade 60-80k€ de valor
- Distribución mejorable → quitar paredes añade valor

BARRIO — POTENCIAL DE REVALORIZACIÓN:
- Lavapiés, Malasaña, Chueca, Chamberí: alta demanda, venta rápida post-reforma
- Carabanchel, Vallecas: más margen de compra pero salida más lenta
- Salamanca, Retiro: tickets altos, menos flippers, más difícil

PENALIZACIONES REALES:
- Sin ascensor en 4ª+ planta: -1 (difícil vender)
- Alquilado con contrato vigente: -2 (no puedes reformar)
- Precio absoluto >500k: -1 (pool de compradores pequeño)
- Ya reformado de lujo: -2 (sin margen de mejora)
- Exterior muy ruidoso o zona conflictiva conocida: -1

WALLAPOP ES POSITIVO: Los mejores deals están en Wallapop y Milanuncios, no en Idealista. Un vendedor en Wallapop suele ser particular, motivado, y con precio negociable. Nunca lo pongas como red flag.

ESCALA REAL:
- 8-10: Operación excepcional, actuar inmediato. Precio/m² bajo + señales reforma + barrio bueno.
- 6-7: Interesante, merece visita y negociación.
- 4-5: Precio de mercado, sin ventaja clara. Solo si hay algo especial.
- 2-3: Caro o sin potencial. Pasar.
- 0-1: Descarte total.

Sé directo. Di exactamente qué margen estimas y por qué. Un score 8+ debe ser una oportunidad real."""


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
        pending = db.query(Listing).filter(Listing.score.is_(None)).all()
        print(f"🔍 {len(pending)} listings pendientes de scoring")

        for listing in pending:
            print(f"\n📊 Scoring: {listing.title[:60]}...")
            try:
                result = await score_listing(listing)
                listing.score = result["score"]
                listing.score_reasoning = result["reasoning"]
                listing.score_green_flags = ", ".join(result.get("green_flags", []))
                listing.score_red_flags = ", ".join(result.get("red_flags", []))
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
