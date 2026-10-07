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

from .base_scraper import (
    PAGE_CAP, REPEATED_PAGE, UNPARSEABLE, BaseScraper, PageWalk, RawListing, ScrapeResult, as_count,
)

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


_TOTAL_RE = re.compile(r"Propiedades\s+encontradas:\s*([\d.]+)", re.IGNORECASE)


def parse_total(soup: BeautifulSoup) -> Optional[int]:
    """Total que informa el sitio: 'Propiedades encontradas: 142' → 142."""
    m = _TOTAL_RE.search(soup.get_text(" ", strip=True))
    return as_count(m.group(1)) if m else None


def count_site_items(soup: BeautifulSoup) -> int:
    """Tarjetas que muestra la página, antes de descartar las que no se pueden parsear."""
    return len(soup.select("div.listingRow"))


class RemaxScraper(BaseScraper):

    def __init__(self):
        super().__init__(source_name="remax")

    async def fetch_listings(self) -> ScrapeResult:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> ScrapeResult:
        walk = PageWalk()
        total = None
        # Si el for termina sin break, se agotó el tope y quedaba catálogo.
        reached_end, stop_reason, error = False, PAGE_CAP, None
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
                error = f"página {page + 1}: {e}"
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            total = total or parse_total(soup)
            cards = self._parse_page(soup)
            site_items = count_site_items(soup)

            if not cards:
                logger.info(f"[remax] Sin resultados en página {page + 1}, parando.")
                # Sin tarjetas en la página: fin del catálogo. Con tarjetas que no se pudieron leer, no.
                if site_items == 0:
                    reached_end = True
                else:
                    stop_reason = UNPARSEABLE
                break

            if not walk.add_page(cards, site_items):
                logger.warning(f"[remax] Página {page + 1} repetida (el sitio ignora ?start), parando.")
                stop_reason = REPEATED_PAGE
                break

            logger.info(f"[remax] {len(cards)} pisos encontrados")
            time.sleep(1.5)

        return ScrapeResult.finish(
            walk.listings, reached_end=reached_end, stop_reason=stop_reason,
            total_reported=total, items_seen=walk.items_seen, error=error,
        )

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
