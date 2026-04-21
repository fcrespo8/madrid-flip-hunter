import asyncio
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from backend.models.database import SessionLocal
from backend.models.listing import Listing

# Ordered from most-specific to most-generic.
# Each pattern captures the numeric value in group 1.
_PATTERNS = [
    # JSON/data attributes: "surface":85, "surface_area":85, "meters":85
    r'"(?:surface_area|surface|metros_cuadrados|meters|floor_size|square_meters)"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
    # Structured label next to value: "85 m²" or "85m²" preceded by common attribute separators
    r'(?:superficie|tamaño|size|area|metros)[^\d]{0,20}(\d+(?:[.,]\d+)?)\s*m[²2]',
    # Plain "85 m²" / "85m²" / "85 m2" anywhere in the HTML
    r'(\d+(?:[.,]\d+)?)\s*m[²2]',
]


def _extract_from_html(html: str) -> float | None:
    for pattern in _PATTERNS:
        for match in re.finditer(pattern, html, re.IGNORECASE):
            raw = match.group(1).replace(",", ".")
            try:
                value = float(raw)
                # Sanity-check: plausible apartment size range
                if 10 <= value <= 1000:
                    return value
            except ValueError:
                continue
    return None


async def enrich_sizes() -> None:
    db = SessionLocal()
    try:
        listings = db.query(Listing).filter(Listing.size_m2.is_(None)).all()
        print(f"[enrich_size] {len(listings)} listings sin tamaño")
        if not listings:
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            for listing in listings:
                context = await browser.new_context(locale="es-ES")
                page = await context.new_page()
                await Stealth().apply_stealth_async(page)

                try:
                    await page.goto(listing.url, wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(2)

                    html = await page.content()
                    size_m2 = _extract_from_html(html)

                    if size_m2 is not None:
                        listing.size_m2 = size_m2
                        db.commit()
                        print(f"[enrich_size] ✓ {listing.external_id}: {size_m2} m²")
                    else:
                        print(f"[enrich_size] ✗ {listing.external_id}: tamaño no encontrado")

                except Exception as e:
                    print(f"[enrich_size] Error en {listing.external_id}: {e}")
                finally:
                    await context.close()

            await browser.close()
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(enrich_sizes())
