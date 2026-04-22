import asyncio
import re
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from .base_scraper import BaseScraper, RawListing

# Known municipalities that are NOT Madrid capital
_NON_MADRID = re.compile(
    r"\b(cercedilla|alcalá|alcala|getafe|leganés|leganes|móstoles|mostoles|"
    r"alcorcón|alcorcon|fuenlabrada|pinto|valdemoro|aranjuez|toledo|"
    r"guadalajara|mejorada|torrejón|torrejon|pozuelo|majadahonda|"
    r"las rozas|boadilla|villalba|collado|tres cantos|colmenar)\b",
    re.IGNORECASE,
)


class DonpisoScraper(BaseScraper):

    SEARCH_URL = "https://www.donpiso.com/venta-casas-y-pisos/madrid/listado"
    BASE_URL = "https://www.donpiso.com"
    _DETAIL_HREF = re.compile(r"/pisos-y-casas/\d+")

    def __init__(self):
        super().__init__(source_name="donpiso")

    async def fetch_listings(self) -> list[RawListing]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                locale="es-ES",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            await page.goto(self.SEARCH_URL, wait_until="networkidle")
            await asyncio.sleep(2)
            html = await page.content()
            await browser.close()

        return self._parse_html(html)

    def _parse_html(self, html: str) -> list[RawListing]:
        soup = BeautifulSoup(html, "html.parser")

        # Anchor on detail links — we know these exist and have the right href
        detail_links = soup.find_all("a", href=self._DETAIL_HREF)
        print(f"[donpiso] {len(detail_links)} detail links found")

        listings = []
        seen = set()

        for i, link in enumerate(detail_links):
            try:
                # DEBUG: print raw HTML of the card area for the first link
                listing = self._parse_card(link)
                if listing and listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)
            except Exception as e:
                print(f"[donpiso] Error parsing card: {e}")

        return listings

    def _parse_card(self, link) -> RawListing | None:
        href = link.get("href", "")
        # Strip query string and fragment — e.g. "298355_apartamento...&cerrar=1#video"
        href = re.split(r"[&?#]", href)[0]
        url = href if href.startswith("http") else self.BASE_URL + href

        # ID from URL: /pisos-y-casas/302400_piso-en-venta-...
        id_match = re.search(r"/pisos-y-casas/(\d+)", href)
        if not id_match:
            return None
        external_id = id_match.group(1)

        # Walk up to find the card container (the div that holds price, title, specs)
        card = link.find_parent("div")
        if card is None:
            return None
        # Keep walking up until we find a div that contains an <img alt="Inmueble">
        for _ in range(5):
            if card.find("img", alt=re.compile(r"Inmueble")):
                break
            parent = card.find_parent("div")
            if parent is None:
                break
            card = parent

        # Title: from img alt, strip "Imagen N Inmueble XXXXXX - " prefix
        title = ""
        img = card.find("img", alt=re.compile(r"Inmueble"))
        if img:
            alt = img.get("alt", "")
            title = re.sub(r"^Imagen\s+\d+\s+Inmueble\s+\d+\s*[-–]\s*", "", alt).strip()

        if not title:
            # Fall back to text inside the link itself
            title = link.get_text(strip=True) or "Sin título"

        # Filter: skip listings outside Madrid capital
        if _NON_MADRID.search(title):
            return None

        # Neighborhood from dedicated CSS class — "Cercedilla", "Madrid", etc.
        neighborhood = None
        zone_el = card.find(class_="item__zone")
        if zone_el:
            neighborhood = zone_el.get_text(strip=True) or None

        # Price from item__price class — handles "89.900 €" (dot = thousands separator)
        price = None
        price_el = card.find(class_="item__price")
        if price_el:
            raw = price_el.get_text(strip=True)
            # Remove dots used as thousands separators, then strip non-digits
            digits = re.sub(r"[^\d]", "", raw.replace(".", ""))
            try:
                price = float(digits) or None
            except ValueError:
                pass

        # m² and rooms — search all text in card
        size_m2, rooms = None, None
        full_text = card.get_text(" ")
        m2_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]", full_text, re.IGNORECASE)
        rooms_match = re.search(r"(\d+)\s*hab", full_text, re.IGNORECASE)
        if m2_match:
            size_m2 = float(m2_match.group(1).replace(",", "."))
        if rooms_match:
            rooms = int(rooms_match.group(1))

        return RawListing(
            source="donpiso",
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            size_m2=size_m2,
            rooms=rooms,
            neighborhood=neighborhood,
            district=None,
            lat=None,
            lon=None,
            description=None,
        )
