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

        for page in range(1, 11):
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

            # Precio
            price = self._parse_price_from_card(link)
            if not price:
                continue

            # Título — h3 dentro del link
            title_tag = link.find("h3") or link.find("h2")
            title = title_tag.get_text(strip=True) if title_tag else "Piso en Madrid"

            # Barrio — formato "{barrio}, Madrid"
            neighborhood = None
            m = re.match(r"^(.+?),\s*Madrid", title, re.IGNORECASE)
            if m:
                neighborhood = m.group(1).strip()

            # Habitaciones y m² — buscar por icono Font Awesome
            rooms = self._parse_icon_value(link, "fa-bed")
            size_m2 = self._parse_icon_value(link, "fa-angle-90", as_float=True)

            results.append(RawListing(
                source="redpiso",
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
            ))

        return results

    def _parse_icon_value(self, card, icon_class: str, as_float: bool = False):
        """Busca <i class='... {icon_class} ...'> y extrae el número del span padre."""
        icon = card.find("i", class_=re.compile(icon_class))
        if not icon:
            return None
        span = icon.find_parent("span")
        if not span:
            return None
        text = span.get_text(strip=True)
        m = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if not m:
            return None
        val = m.group(1).replace(",", ".")
        return float(val) if as_float else int(float(val))

    def _parse_price_from_card(self, card) -> Optional[float]:
        """Busca el precio en el span con clase text-red-500 o similar."""
        # Buscar span dentro de p con clase que contenga "red"
        price_el = card.find("p", class_=re.compile(r"red"))
        if price_el:
            raw = price_el.get_text(strip=True)
            digits = re.sub(r"[^\d]", "", raw.replace(".", ""))
            return float(digits) if digits else None

        # Fallback: buscar cualquier texto con patrón de precio
        text = card.get_text(" ", strip=True)
        m = re.search(r"([\d.]+)\s*€", text)
        if m:
            try:
                return float(m.group(1).replace(".", ""))
            except ValueError:
                return None
        return None
