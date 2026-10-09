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

from .base_scraper import (
    BAD_PAGINATION, HTTP_ERROR, PAGE_CAP, REPEATED_PAGE, BaseScraper, RawListing, ScrapeResult, as_count,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
PAGE_DELAY_S = 1.0         # pausa entre páginas
MAX_RETRIES = 2            # reintentos por página tras el primer intento (hasta 3 requests)
RETRY_BACKOFF_S = 2.0      # espera antes de cada reintento: 2 s, 4 s, ...
# Cinturón de seguridad ante una API que nunca termina; NO es un tope de scraping
# (el recorrido llega hasta el total_pages que informa la API: hoy son 62 páginas).
MAX_PAGES_SAFETY = 200

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


def _is_retryable(exc: requests.RequestException) -> bool:
    """Reintentar lo transitorio (red, timeout, 5xx, 429, 408, JSON roto). Un 4xx como
    403 o 404 no se arregla esperando: se corta de una."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None and 400 <= status < 500:
        return status in (408, 429)
    return True


class TecnocasaScraper(BaseScraper):

    def __init__(self):
        super().__init__(source_name="tecnocasa")

    async def fetch_listings(self) -> ScrapeResult:
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> ScrapeResult:
        session = requests.Session()
        session.headers.update(HEADERS)

        # 1. Obtener todas las coordenadas en una sola llamada
        coords = self._fetch_coords(session)
        logger.info(f"[tecnocasa] {len(coords)} coordenadas obtenidas")

        # 2. Paginar hasta el total_pages que informa la API y cruzar con coordenadas
        listings = []
        seen = set()
        site_ids = set()   # ids que el sitio listó, antes de descartar los que no se pueden parsear
        total_items = total_pages = None
        # Si el for termina sin break, se llegó al cinturón de seguridad y quedaba catálogo.
        reached_end, stop_reason, error = False, PAGE_CAP, None

        for page in range(1, MAX_PAGES_SAFETY + 1):
            params = dict(BASE_PARAMS)
            if page > 1:
                params["page"] = page

            data, error = self._fetch_page(session, params, page, total_pages)
            if error:
                logger.error(f"[tecnocasa] {error}")
                break

            estates = data.get("estates", [])
            if not estates:
                logger.info(f"[tecnocasa] Sin resultados en página {page}, parando.")
                reached_end = True
                break

            page_ids = {e["id"] for e in estates if e.get("id")}
            if page_ids and page_ids <= site_ids:
                logger.warning(f"[tecnocasa] Página {page} repetida, parando.")
                stop_reason = REPEATED_PAGE
                break
            site_ids |= page_ids

            for estate in estates:
                listing = self._parse_estate(estate, coords)
                if listing and listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)

            pagination = data.get("pagination") or {}
            total_pages = as_count(pagination.get("total_pages"))
            total_items = as_count(pagination.get("total_items")) or total_items
            if total_pages is None:
                # Sin total_pages no se sabe si esta fue la última página: no asumirlo.
                logger.error(f"[tecnocasa] La API no informó total_pages en la página {page}, parando.")
                stop_reason = BAD_PAGINATION
                break
            logger.info(f"[tecnocasa] Página {page}/{total_pages}: {len(estates)} pisos")

            if page >= total_pages:
                reached_end = True
                break

            time.sleep(PAGE_DELAY_S)

        return ScrapeResult.finish(
            listings, reached_end=reached_end, stop_reason=stop_reason,
            total_reported=total_items, items_seen=len(site_ids), error=error, error_reason=HTTP_ERROR,
        )

    def _fetch_page(self, session: requests.Session, params: dict, page: int,
                    total_pages: Optional[int]) -> tuple[Optional[dict], Optional[str]]:
        """GET de una página con reintentos y backoff. Devuelve (datos, None) o (None, error),
        con el error explicando en qué página se cortó y tras cuántos intentos."""
        attempts = 0
        while True:
            attempts += 1
            try:
                resp = session.get(f"{BASE_API}/search", params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                return resp.json(), None
            except requests.RequestException as e:
                if attempts > MAX_RETRIES or not _is_retryable(e):
                    of = f" de {total_pages}" if total_pages else ""
                    word = "intento" if attempts == 1 else "intentos"
                    return None, f"página {page}{of}: {type(e).__name__}: {e} (tras {attempts} {word})"
                delay = RETRY_BACKOFF_S * 2 ** (attempts - 1)
                logger.warning(f"[tecnocasa] Página {page}: {type(e).__name__}: {e}; "
                               f"reintento {attempts}/{MAX_RETRIES} en {delay:g}s")
                time.sleep(delay)

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
