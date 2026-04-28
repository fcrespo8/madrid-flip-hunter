"""
RedpisoScraper — redpiso.es (Nuxt SSR, HTTP puro, sin Playwright)
URL: /venta-viviendas/madrid
Paginación: ?page=2, ?page=3, ...
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

BASE_URL = "https://www.redpiso.es/venta-viviendas/madrid"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.redpiso.es/",
}

_NON_MADRID = re.compile(
    r"-(alcala-de-henares|getafe|mostoles|alcorcon|leganes|fuenlabrada|"
    r"pozuelo|alcobendas|las-rozas|majadahonda|pinto|collado-villalba|"
    r"torrelodones|ciempozuelos|arganda|rivas-vaciamadrid)-",
    re.IGNORECASE,
)


class RedpisoScraper(BaseScraper):

    def __init__(self):
        super().__init__(source_name="redpiso")

    async def fetch_listings(self) -> list[RawListing]:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[RawListing]:
        listings = []
        session = requests.Session()
        session.headers.update(HEADERS)

        for page in range(1, 11):  # máx 10 páginas
            url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
            logger.info(f"[redpiso] Página {page}: {url}")

            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"[redpiso] Error: {e}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = self._parse_page(soup)

            if not cards:
                logger.info(f"[redpiso] Sin resultados en página {page}, parando.")
                break

            listings.extend(cards)
            logger.info(f"[redpiso] {len(cards)} pisos encontrados")
            time.sleep(1.5)

        return listings

    def _parse_page(self, soup: BeautifulSoup) -> list[RawListing]:
        results = []
        seen = set()

        links = soup.select("a[href*='/inmueble/']")

        for link in links:
            href = link.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)

            if _NON_MADRID.search(href):
                continue

            full_url = href if href.startswith("http") else f"https://www.redpiso.es{href}"

            ref_match = re.search(r"(RP\d+)$", href.rstrip("/"))
            external_id = ref_match.group(1) if ref_match else href.split("/")[-1]

            text = link.get_text(" ", strip=True)
            price = self._parse_price(text)
            if not price:
                continue

            neighborhood, district = self._parse_location(href)

            title_tag = link.find(["h3", "h2"])
            title = title_tag.get_text(strip=True) if title_tag else f"Piso en {neighborhood or 'Madrid'}"

            results.append(RawListing(
                source="redpiso",
                external_id=external_id,
                url=full_url,
                title=title,
                price=price,
                size_m2=self._parse_size(text),
                rooms=self._parse_rooms(text),
                neighborhood=neighborhood,
                district=district,
                lat=None,
                lon=None,
                description=None,
            ))

        return results

    def _parse_location(self, href: str) -> tuple[Optional[str], Optional[str]]:
        """
        Slug: /inmueble/piso-en-venta-en-{calle}-{barrio}-{distrito}-madrid-RP{id}
        Extrae los dos segmentos antes de 'madrid'.
        """
        slug = href.split("/inmueble/")[-1]
        slug = re.sub(r"^(piso|casa|duplex|atico|estudio|loft)-en-venta-en-", "", slug)
        slug = re.sub(r"-RP\d+$", "", slug)
        parts = slug.split("-")

        try:
            idx = parts.index("madrid")
            district = parts[idx - 1].title() if idx >= 1 else None
            neighborhood = parts[idx - 2].title() if idx >= 2 else district
            return neighborhood, district
        except ValueError:
            return None, None

    def _parse_price(self, text: str) -> Optional[float]:
        m = re.search(r"([\d.]+)\s*€", text)
        if m:
            try:
                return float(m.group(1).replace(".", ""))
            except ValueError:
                return None
        return None

    def _parse_size(self, text: str) -> Optional[float]:
        m = re.search(r"(\d+)\s*m²", text)
        return float(m.group(1)) if m else None

    def _parse_rooms(self, text: str) -> Optional[int]:
        # Redpiso: texto empieza "3 2 102 m²" → primer número = habitaciones
        m = re.match(r"^(\d+)\s+\d+\s+\d+\s*m²", text)
        return int(m.group(1)) if m else None
