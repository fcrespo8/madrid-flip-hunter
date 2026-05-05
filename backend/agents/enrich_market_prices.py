"""
enrich_market_prices — scrapes precios medios €/m² por distrito de Idealista.
Usa Playwright porque Idealista bloquea HTTP puro.
Se ejecuta una vez al mes — los datos no cambian tan rápido.
"""
import asyncio
import re
import logging
from datetime import datetime
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from backend.models.database import SessionLocal
from backend.models.listing import Listing

logger = logging.getLogger(__name__)

DISTRITOS = {
    "arganzuela": "Arganzuela",
    "barajas": "Barajas",
    "carabanchel": "Carabanchel",
    "centro": "Centro",
    "chamartin": "Chamartín",
    "chamberi": "Chamberí",
    "ciudad-lineal": "Ciudad Lineal",
    "fuencarral": "Fuencarral",
    "hortaleza": "Hortaleza",
    "latina": "Latina",
    "moncloa": "Moncloa",
    "moratalaz": "Moratalaz",
    "puente-de-vallecas": "Puente de Vallecas",
    "retiro": "Retiro",
    "salamanca": "Salamanca",
    "san-blas": "San Blas",
    "tetuan": "Tetuán",
    "usera": "Usera",
    "vicalvaro": "Vicálvaro",
    "villa-de-vallecas": "Villa de Vallecas",
    "villaverde": "Villaverde",
}

BASE_URL = "https://www.idealista.com/sala-de-prensa/informes-precio-vivienda/venta/madrid-comunidad/madrid-provincia/madrid/{slug}/"

# Guardamos en memoria — tabla simple en BD no es necesaria por ahora
# Se usa como dict {distrito_nombre: precio_m2} inyectado en el scoring agent
_CACHE: dict[str, float] = {}


async def fetch_all_prices() -> dict[str, float]:
    """Devuelve {nombre_distrito: precio_m2} para los 21 distritos."""
    global _CACHE
    prices = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="es-ES", user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        for slug, nombre in DISTRITOS.items():
            url = BASE_URL.format(slug=slug)
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                # Buscar td con data-sortable (valor numérico exacto)
                el = await page.query_selector("td[data-sortable]")
                if el:
                    raw = await el.get_attribute("data-sortable")
                    price = float(raw)
                    prices[nombre] = price
                    logger.info(f"[market_prices] {nombre}: {price:.0f} €/m²")
                else:
                    logger.warning(f"[market_prices] {nombre}: precio no encontrado")
            except Exception as e:
                logger.error(f"[market_prices] Error en {nombre}: {e}")

        await browser.close()

    _CACHE = prices
    return prices


def get_cached_prices() -> dict[str, float]:
    return _CACHE


def get_price_for_district(district: str) -> float | None:
    """Busca precio medio para un distrito dado (matching flexible)."""
    if not district or not _CACHE:
        return None
    # Match exacto
    if district in _CACHE:
        return _CACHE[district]
    # Match parcial case-insensitive
    district_lower = district.lower()
    for nombre, precio in _CACHE.items():
        if nombre.lower() in district_lower or district_lower in nombre.lower():
            return precio
    return None


if __name__ == "__main__":
    async def main():
        prices = await fetch_all_prices()
        print(f"\n=== Precios medios Madrid {datetime.now().strftime('%B %Y')} ===")
        for distrito, precio in sorted(prices.items(), key=lambda x: x[1], reverse=True):
            print(f"  {distrito:<25} {precio:>8.0f} €/m²")
    asyncio.run(main())
