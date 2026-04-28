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
        # HTTP puro — no necesita async, pero BaseScraper lo requiere
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[RawListing]:
        listings = []
        session = requests.Session()
        session.headers.update(HEADERS)

        for page in range(10):  # máx ~200 pisos
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

        links = soup.select("a[href*='/buscador-de-inmuebles/venta/piso/madrid/']")

        for link in links:
            href = link.get("href", "")
            if not href or href in seen:
                continue

            # Solo links de detalle — tienen al menos 8 segmentos en el path
            parts = [p for p in href.rstrip("/").split("/") if p]
            if len(parts) < 8:
                continue

            seen.add(href)
            text = link.get_text(" ", strip=True)
            price = self._parse_price(text)
            if not price:
                continue

            external_id = parts[-1]
            neighborhood = parts[-2].replace("-", " ").title()
            full_url = href if href.startswith("http") else f"https://www.remax.es{href}"

            title_tag = link.find(["h2", "h3"])
            title = title_tag.get_text(strip=True) if title_tag else f"Piso en {neighborhood}"

            results.append(RawListing(
                source="remax",
                external_id=external_id,
                url=full_url,
                title=title,
                price=price,
                size_m2=self._parse_size(text),
                rooms=self._parse_rooms(text),
                neighborhood=neighborhood,
                district=None,
                lat=None,
                lon=None,
                description=None,
            ))

        return results

    def _parse_price(self, text: str) -> Optional[float]:
        m = re.search(r"([\d.]+)\s*€", text)
        if m:
            try:
                return float(m.group(1).replace(".", ""))
            except ValueError:
                return None
        return None

    def _parse_size(self, text: str) -> Optional[float]:
        m = re.search(r"(\d+)\s*m2", text, re.IGNORECASE)
        return float(m.group(1)) if m else None

    def _parse_rooms(self, text: str) -> Optional[int]:
        m = re.search(r"(\d+)\s*hab", text, re.IGNORECASE)
        return int(m.group(1)) if m else None
