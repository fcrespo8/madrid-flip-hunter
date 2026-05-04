"""
TecnocasaScraper — tecnocasa.es (API REST, HTTP puro, sin Playwright)
API: /api/estates/search (datos) + /api/estates/search-map-list (coordenadas)
930 pisos en Madrid, 62 páginas de 15
"""
import re
import time
import asyncio
import logging
from typing import Optional

import requests

from .base_scraper import BaseScraper, RawListing

logger = logging.getLogger(__name__)

BASE_API = "https://www.tecnocasa.es/api/estates"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.tecnocasa.es/",
}

BASE_PARAMS = {
    "city": "5312",
    "contract": "acquis",
    "sector": "res",
    "section": "estate",
    "province": "M",
    "region": "cma",
}


class TecnocasaScraper(BaseScraper):

    def __init__(self):
        super().__init__(source_name="tecnocasa")

    async def fetch_listings(self) -> list[RawListing]:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> list[RawListing]:
        session = requests.Session()
        session.headers.update(HEADERS)

        # 1. Obtener todas las coordenadas en una sola llamada
        coords = self._fetch_coords(session)
        logger.info(f"[tecnocasa] {len(coords)} coordenadas obtenidas")

        # 2. Paginar listings y cruzar con coordenadas
        listings = []
        seen = set()
        max_pages = 20  # máx ~300 pisos por ejecución

        for page in range(1, max_pages + 1):
            params = dict(BASE_PARAMS)
            if page > 1:
                params["page"] = page

            try:
                resp = session.get(f"{BASE_API}/search", params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                logger.error(f"[tecnocasa] Error página {page}: {e}")
                break

            estates = data.get("estates", [])
            if not estates:
                logger.info(f"[tecnocasa] Sin resultados en página {page}, parando.")
                break

            for estate in estates:
                listing = self._parse_estate(estate, coords)
                if listing and listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)

            total_pages = data.get("pagination", {}).get("total_pages", 1)
            logger.info(f"[tecnocasa] Página {page}/{min(max_pages, total_pages)}: {len(estates)} pisos")

            if page >= total_pages:
                break

            time.sleep(1.0)

        return listings

    def _fetch_coords(self, session: requests.Session) -> dict[int, tuple[float, float]]:
        """Obtiene {id: (lat, lon)} para todos los pisos en una sola llamada."""
        coords = {}
        params = dict(BASE_PARAMS)
        params["zoom"] = "14"

        try:
            resp = session.get(f"{BASE_API}/search-map-list", params=params, timeout=20)
            resp.raise_for_status()
            features = resp.json().get("collection", {}).get("features", [])
            for f in features:
                estate_id = f.get("id")
                geometry = f.get("geometry", {})
                coordinates = geometry.get("coordinates", [])
                if estate_id and len(coordinates) == 2:
                    lon, lat = coordinates  # GeoJSON: [lng, lat]
                    coords[int(estate_id)] = (float(lat), float(lon))
        except requests.RequestException as e:
            logger.error(f"[tecnocasa] Error obteniendo coordenadas: {e}")

        return coords

    def _parse_estate(self, estate: dict, coords: dict) -> Optional[RawListing]:
        estate_id = estate.get("id")
        if not estate_id:
            return None

        # Precio — "175.000 €" → 175000.0
        price = self._parse_price(estate.get("price", ""))
        if not price:
            return None

        # m² — "35 m<sup>2</sup>" → 35.0
        size_m2 = self._parse_size(estate.get("surface", ""))

        # Habitaciones — "2 dorm." → 2
        rooms = self._parse_rooms(estate.get("rooms", ""))

        # Barrio — "Madrid, Tetuán" → "Tetuán"
        neighborhood, district = self._parse_location(estate.get("subtitle", ""))

        # Coordenadas del mapa
        lat, lon = None, None
        if estate_id in coords:
            lat, lon = coords[estate_id]

        url = estate.get("detail_url", f"https://www.tecnocasa.es/venta/piso/madrid/madrid/{estate_id}.html")
        title = estate.get("title", "Piso en venta")

        return RawListing(
            source="tecnocasa",
            external_id=str(estate_id),
            url=url,
            title=f"{title} — {neighborhood or 'Madrid'}",
            price=price,
            size_m2=size_m2,
            rooms=rooms,
            neighborhood=neighborhood,
            district=district,
            lat=lat,
            lon=lon,
            description=None,
        )

    def _parse_price(self, raw: str) -> Optional[float]:
        digits = re.sub(r"[^\d]", "", raw)
        return float(digits) if digits else None

    def _parse_size(self, raw) -> Optional[float]:
        if not raw:
            return None
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", str(raw))
        if m:
            return float(m.group(1).replace(",", "."))
        return None

    def _parse_rooms(self, raw) -> Optional[int]:
        if not raw:
            return None
        m = re.search(r"(\d+)", str(raw))
        return int(m.group(1)) if m else None

    def _parse_location(self, subtitle: str) -> tuple[Optional[str], Optional[str]]:
        # "Madrid, Tetuán" → neighborhood="Tetuán", district="Tetuán"
        # "Madrid, Tetuán, Bellas Vistas" → neighborhood="Bellas Vistas", district="Tetuán"
        parts = [p.strip() for p in subtitle.split(",")]
        # Quitar "Madrid" del principio
        parts = [p for p in parts if p.lower() != "madrid"]

        if len(parts) >= 2:
            return parts[1], parts[0]  # barrio, distrito
        elif len(parts) == 1:
            return parts[0], parts[0]
        return None, None
