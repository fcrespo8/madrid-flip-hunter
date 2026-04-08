import asyncio
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from .base_scraper import BaseScraper, RawListing


class WallapopScraper(BaseScraper):

    SEARCH_URL = (
        "https://es.wallapop.com/app/search"
        "?keywords=piso+en+venta+madrid"
        "&category_ids=200"
        "&longitude=-3.7037902"
        "&latitude=40.4167754"
        "&distance=15000"
    )

    def __init__(self):
        super().__init__(source_name="wallapop")

    async def fetch_listings(self) -> list[RawListing]:
        api_data = None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(locale="es-ES")
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            async def intercept(response):
                nonlocal api_data
                if "search/section" in response.url:
                    try:
                        api_data = await response.json()
                    except Exception:
                        pass

            page.on("response", intercept)
            await page.goto(self.SEARCH_URL, wait_until="networkidle")
            await asyncio.sleep(3)
            await browser.close()

        if not api_data:
            print("[wallapop] No se interceptó la API")
            return []

        items = api_data.get("data", {}).get("section", {}).get("items", [])
        print(f"[wallapop] {len(items)} items encontrados en la API")

        listings = []
        seen = set()

        for item in items:
            try:
                listing = self._parse_item(item)
                if listing and listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)
            except Exception as e:
                print(f"[wallapop] Error parseando item {item.get('id')}: {e}")

        return listings

    def _parse_item(self, item: dict) -> RawListing | None:
        item_id = item.get("id")
        if not item_id:
            return None

        web_slug = item.get("web_slug", "")
        url = f"https://es.wallapop.com/item/{web_slug}" if web_slug else f"https://es.wallapop.com/item/{item_id}"

        title = item.get("title", "Sin título")
        description = item.get("description", "")

        price_data = item.get("price", {})
        price = price_data.get("amount")

        location = item.get("location", {})
        lat = location.get("latitude")
        lon = location.get("longitude")
        city = location.get("city", "")

        type_attrs = item.get("type_attributes", {})
        operation = type_attrs.get("operation", "")
        rooms = type_attrs.get("rooms")

        # Extraemos barrio del título: "Piso en venta en Chueca en Madrid"
        neighborhood = None
        district = None
        match = re.search(r"en (.+?) en Madrid", title, re.IGNORECASE)
        if match:
            neighborhood = match.group(1).strip()

        return RawListing(
            source="wallapop",
            external_id=item_id,
            url=url,
            title=title,
            price=price,
            size_m2=None,          # No disponible en search API, pendiente
            rooms=rooms,
            neighborhood=neighborhood,
            district=district,
            lat=lat,
            lon=lon,
            description=description,
        )
