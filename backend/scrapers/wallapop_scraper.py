from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from .base_scraper import BaseScraper, RawListing
import re
import asyncio


class WallapopScraper(BaseScraper):

    BASE_URL = "https://es.wallapop.com/app/search?keywords=piso+en+venta+madrid&category_ids=200&longitude=-3.7037902&latitude=40.4167754&distance=15000"

    def __init__(self):
        super().__init__(source_name="wallapop")

    async def fetch_listings(self) -> list[RawListing]:
        listings = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                locale="es-ES",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            await page.goto(self.BASE_URL, wait_until="networkidle")
            await asyncio.sleep(3)

            # Scroll para cargar más resultados
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)

            html = await page.content()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("a[href*='/item/']")

        seen = set()
        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)
            except Exception as e:
                print(f"[wallapop] Error parseando card: {e}")
                continue

        return listings

    def _parse_card(self, card) -> RawListing | None:
        href = card.get("href", "")
        if not href:
            return None

        external_id = href.split("/item/")[-1].split("?")[0]
        url = "https://es.wallapop.com" + href if href.startswith("/") else href

        title_el = card.select_one("[class*='title'], [class*='Title']")
        price_el = card.select_one("[class*='price'], [class*='Price']")

        title = title_el.get_text(strip=True) if title_el else "Sin título"
        if len(title) < 3:
            return None

        price = None
        if price_el:
            price_text = price_el.get_text(strip=True)
            match = re.search(r"[\d.,]+", price_text.replace(".", "").replace(",", ""))
            if match:
                try:
                    price = float(match.group())
                except ValueError:
                    pass

        return RawListing(
            source="wallapop",
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            size_m2=None,
            rooms=None,
            neighborhood=None,
            district=None,
            lat=None,
            lon=None,
            description=None,
        )
