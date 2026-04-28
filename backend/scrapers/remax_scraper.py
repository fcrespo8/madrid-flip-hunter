"""
RemaxScraper — remax.es (WordPress SSR, HTTP puro, sin Playwright)
URL: /buscador-de-inmuebles/venta/piso/madrid/madrid/todos/
Paginación: ?start=20, ?start=40, ...
"""
import re
import time
import asyncio
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base_scraper import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_URL = "https://www.remax.es/buscador-de-inmuebles/venta/piso/madrid/madrid/todos/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


class RemaxScraper(BaseScraper):

    def __init__(self):
        super().__init__(source_name="remax")

    async def fetch_listings(self) -> list[RawListing]:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[RawListing]:
        listings = []
        session = requests.Session()
        session.headers.update(HEADERS)

        for page in range(10):
            start = page * 20
            url = BASE_URL if start == 0 else f"{BASE_URL}?start={start}"
            logger.info(f"[remax] Página {page + 1}: {url}")

            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"[remax] Error: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = self._parse_page(soup)

            if not cards:
                logger.info(f"[remax] Sin resultados en página {page + 1}, parando.")
                break

            listings.extend(cards)
            logger.info(f"[remax] {len(cards)} pisos encontrados")
            time.sleep(1.5)

        return listings

    def _parse_page(self, soup: BeautifulSoup) -> list[RawListing]:
        results = []
        seen = set()

        cards = soup.select("div.listingRow")
        logger.info(f"[remax] {len(cards)} cards en HTML")

        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing and listing.external_id not in seen:
                    seen.add(listing.external_id)
                    results.append(listing)
            except Exception as e:
                logger.warning(f"[remax] Error parseando card: {e}")

        return results

    def _parse_card(self, card) -> Optional[RawListing]:
        # ID y URL — link con clase "enlace_{id}"
        link = card.find("a", class_=re.compile(r"^enlace_"))
        if not link:
            return None

        href = link.get("href", "")
        if not href:
            return None

        full_url = href if href.startswith("http") else f"https://www.remax.es{href}"

        id_match = re.search(r"enlace_(\d+)", " ".join(link.get("class", [])))
        external_id = id_match.group(1) if id_match else href.split("/")[-1]

        # Precio
        price_el = card.find(class_="inmueble-detalle-precio")
        price = None
        if price_el:
            raw = price_el.get_text(strip=True)
            digits = re.sub(r"[^\d]", "", raw.replace(".", ""))
            price = float(digits) if digits else None

        if not price:
            return None

        # Título
        title_el = card.find(class_=re.compile(r"inmueble-detalle-nombre"))
        title = title_el.get_text(strip=True) if title_el else "Piso en Madrid"

        # m², habitaciones, baños
        # Hay 3 divs con clase "inmueble-detalle-datos":
        #   1º habitaciones, 2º baños, 3º m² (el que tiene <sup>2</sup>)
        size_m2 = None
        rooms = None

        for block in card.find_all(class_="inmueble-detalle-datos"):
            text = block.get_text(" ", strip=True)
            if block.find("sup"):
                # Este bloque contiene m²
                m = re.search(r"(\d+(?:[.,]\d+)?)", text)
                if m:
                    size_m2 = float(m.group(1).replace(",", "."))
            elif "hab" in text:
                m = re.search(r"(\d+)", text)
                if m:
                    rooms = int(m.group(1))

        # Barrio — del título "Piso en venta, Chamberí - Arapiles, Madrid"
        neighborhood = None
        title_match = re.search(r",\s*([^,]+),\s*Madrid", title, re.IGNORECASE)
        if title_match:
            neighborhood = title_match.group(1).strip()

        return RawListing(
            source="remax",
            external_id=external_id,
            url=full_url,
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
