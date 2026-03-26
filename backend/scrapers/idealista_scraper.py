from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from .base_scraper import BaseScraper, RawListing
import re


class IdealistaScraper(BaseScraper):

    BASE_URL = "https://www.idealista.com/venta-viviendas/madrid-madrid/"

    def __init__(self):
        super().__init__(source_name="idealista")

    async def fetch_listings(self) -> list[RawListing]:
        listings = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.set_extra_http_headers({
                "Accept-Language": "es-ES,es;q=0.9",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })

            await page.goto(self.BASE_URL, wait_until="networkidle")
            html = await page.content()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")
        articles = soup.select("article.item")

        for article in articles:
            try:
                listing = self._parse_article(article)
                if listing:
                    listings.append(listing)
            except Exception as e:
                print(f"[idealista] Error parseando artículo: {e}")
                continue

        return listings

    def _parse_article(self, article) -> RawListing | None:
        title_el = article.select_one(".item-link")
        price_el = article.select_one(".item-price")
        detail_el = article.select_one(".item-detail-char")

        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        url = "https://www.idealista.com" + title_el.get("href", "")
        external_id = re.search(r"/inmueble/(\d+)/", url)
        external_id = external_id.group(1) if external_id else url

        price = None
        if price_el:
            price_text = price_el.get_text(strip=True).replace(".", "").replace("€", "")
            try:
                price = float(re.sub(r"[^\d]", "", price_text))
            except ValueError:
                pass

        size_m2, rooms = None, None
        if detail_el:
            details = detail_el.get_text(strip=True)
            m2_match = re.search(r"(\d+)\s*m²", details)
            rooms_match = re.search(r"(\d+)\s*hab", details)
            size_m2 = float(m2_match.group(1)) if m2_match else None
            rooms = int(rooms_match.group(1)) if rooms_match else None

        return RawListing(
            source="idealista",
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            size_m2=size_m2,
            rooms=rooms,
            neighborhood=None,
            district=None,
            lat=None,
            lon=None,
            description=None,
        )
