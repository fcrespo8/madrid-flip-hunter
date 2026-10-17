"""
DonpisoScraper — donpiso.com (HTML servido ya armado, HTTP puro, sin Playwright)
URL: /venta-casas-y-pisos/madrid/listado (página 1) y /pagina-N.html (N >= 2)

Cómo termina el catálogo (verificado contra el sitio, 2026-10):
  - Cada página con más resultados trae <link rel="next"> y enlaces a páginas posteriores.
  - Desde la página 2 el título dice "(pag. N de M)"; la página 1 no lleva ese sufijo.
  - La última página no tiene rel=next. Pedir una página fuera de rango NO da 404: devuelve
    HTTP 200 con una página genérica sin listings; por eso el scraper nunca la pide.
  - El sitio no muestra el total de listings, solo el de páginas (total_reported queda en None).
HTML plano y Playwright dan exactamente los mismos listings, así que no hace falta navegador.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base_scraper import (
    BAD_PAGINATION, EMPTY, HTTP_ERROR, PAGE_CAP, REPEATED_PAGE, BaseScraper, RawListing, ScrapeResult, _is_retryable,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

REQUEST_TIMEOUT = 30       # las páginas pesan 300-900 KB
PAGE_DELAY_S = 1.0         # pausa entre páginas
MAX_RETRIES = 2            # reintentos por página tras el primer intento (hasta 3 requests)
RETRY_BACKOFF_S = 2.0      # espera antes de cada reintento: 2 s, 4 s, ...
# Cinturón de seguridad ante un sitio que nunca termina; NO es un tope de scraping
# (el recorrido sigue hasta que la paginación del sitio se acaba: hoy son 5 páginas).
MAX_PAGES_SAFETY = 50

# Known municipalities that are NOT Madrid capital
_NON_MADRID = re.compile(
    r"\b(cercedilla|alcalá|alcala|getafe|leganés|leganes|móstoles|mostoles|"
    r"alcorcón|alcorcon|fuenlabrada|pinto|valdemoro|aranjuez|toledo|"
    r"guadalajara|mejorada|torrejón|torrejon|pozuelo|majadahonda|"
    r"las rozas|boadilla|villalba|collado|tres cantos|colmenar)\b",
    re.IGNORECASE,
)

_DETAIL_ID = re.compile(r"/pisos-y-casas/(\d+)")
_PAGE_LINK = re.compile(r"/madrid/pagina-(\d+)\.html")
_TITLE_PAGE = re.compile(r"\(pag\.\s*(\d+)\s+de\s+(\d+)\)", re.IGNORECASE)


@dataclass
class PageNav:
    """Lo que la página dice sobre su lugar en la paginación."""
    has_next: bool                    # <link rel="next">
    max_linked_page: int              # mayor N entre los enlaces pagina-N.html del cuerpo (0 si no hay)
    title_page: Optional[int]         # N de "(pag. N de M)" en el título
    title_total: Optional[int]        # M de "(pag. N de M)" en el título


def page_item_ids(soup: BeautifulSoup) -> set[str]:
    """IDs de los anuncios que muestra la página, antes de descartar los que no son de Madrid
    capital o no tienen precio."""
    return {m.group(1) for a in soup.find_all("a", href=_DETAIL_ID) if (m := _DETAIL_ID.search(a["href"]))}


def parse_nav(soup: BeautifulSoup) -> PageNav:
    has_next = any("next" in (link.get("rel") or []) for link in soup.find_all("link", rel=True))
    linked = [int(m.group(1)) for a in soup.find_all("a", href=_PAGE_LINK) if (m := _PAGE_LINK.search(a["href"]))]
    title = soup.title.string if soup.title and soup.title.string else ""
    m = _TITLE_PAGE.search(title)
    return PageNav(
        has_next=has_next,
        max_linked_page=max(linked, default=0),
        title_page=int(m.group(1)) if m else None,
        title_total=int(m.group(2)) if m else None,
    )


def nav_verdict(nav: PageNav, page: int) -> tuple[str, Optional[str]]:
    """Qué hacer después de la página `page`: ("continue", None), ("end", None) o ("stop", motivo).

    Fin natural solo si todas las señales coinciden: sin rel=next, sin enlaces a páginas
    posteriores y, si el título informa "pag. N de M", N = M = página actual. Cualquier
    contradicción corta como incompleta (bad_pagination): no se asume que terminó."""
    if nav.title_page is not None and nav.title_page != page:
        return "stop", BAD_PAGINATION                 # el sitio sirvió otra página que la pedida
    more = nav.has_next or nav.max_linked_page > page
    if more:
        if nav.title_total is not None and page >= nav.title_total:
            return "stop", BAD_PAGINATION             # dice que hay siguiente, pero que esta es la última
        return "continue", None
    if nav.title_total is not None and nav.title_total != page:
        return "stop", BAD_PAGINATION                 # sin siguiente, pero el título dice "pag. 3 de 5"
    return "end", None


class DonpisoScraper(BaseScraper):

    SEARCH_URL = "https://www.donpiso.com/venta-casas-y-pisos/madrid/listado"
    PAGE_URL = "https://www.donpiso.com/venta-casas-y-pisos/madrid"
    BASE_URL = "https://www.donpiso.com"
    _DETAIL_HREF = re.compile(r"/pisos-y-casas/\d+")

    def __init__(self):
        super().__init__(source_name="donpiso")

    async def fetch_listings(self) -> ScrapeResult:
        return await asyncio.to_thread(self._fetch_sync)

    def _page_url(self, page: int) -> str:
        return self.SEARCH_URL if page == 1 else f"{self.PAGE_URL}/pagina-{page}.html"

    def _fetch_sync(self) -> ScrapeResult:
        session = requests.Session()
        session.headers.update(HEADERS)

        listings = []
        seen = set()
        site_ids = set()   # ids que el sitio listó, antes de descartar los que no son de Madrid o sin precio
        expected_pages = None
        # Si el for termina sin break, se llegó al cinturón de seguridad y quedaba catálogo.
        reached_end, stop_reason, error = False, PAGE_CAP, None

        for page in range(1, MAX_PAGES_SAFETY + 1):
            html, error = self._fetch_page(session, self._page_url(page), page, expected_pages)
            if error:
                logger.error(f"[donpiso] {error}")
                break

            soup = BeautifulSoup(html, "html.parser")
            ids = page_item_ids(soup)
            nav = parse_nav(soup)
            expected_pages = nav.title_total or expected_pages

            if not ids:
                # Fuera de rango el sitio responde 200 con una página sin listings. Como solo se
                # pide una página si la anterior prometía otra, una vacía no es el fin: puede ser
                # un bloqueo o un cambio del sitio.
                logger.warning(f"[donpiso] Página {page} sin listings, parando.")
                stop_reason = EMPTY if page == 1 else BAD_PAGINATION
                break

            if ids <= site_ids:
                logger.warning(f"[donpiso] Página {page} repetida, parando.")
                stop_reason = REPEATED_PAGE
                break
            site_ids |= ids

            for listing in self._parse_soup(soup):
                if listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)

            verdict, reason = nav_verdict(nav, page)
            logger.info(f"[donpiso] Página {page}/{expected_pages or '?'}: {len(ids)} anuncios, {verdict}")
            if verdict == "end":
                reached_end = True
                break
            if verdict == "stop":
                logger.warning(f"[donpiso] Paginación inconsistente en la página {page}: {nav}")
                stop_reason = reason
                break

            time.sleep(PAGE_DELAY_S)

        # El sitio no muestra el total de anuncios (solo el de páginas): sin total_reported.
        return ScrapeResult.finish(
            listings, reached_end=reached_end, stop_reason=stop_reason,
            total_reported=None, items_seen=len(site_ids), error=error, error_reason=HTTP_ERROR,
        )

    def _fetch_page(self, session: requests.Session, url: str, page: int,
                    total_pages: Optional[int]) -> tuple[Optional[str], Optional[str]]:
        """GET de una página con reintentos y backoff. Devuelve (html, None) o (None, error),
        con el error explicando en qué página se cortó y tras cuántos intentos."""
        attempts = 0
        while True:
            attempts += 1
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                return resp.text, None
            except requests.RequestException as e:
                if attempts > MAX_RETRIES or not _is_retryable(e):
                    of = f" de {total_pages}" if total_pages else ""
                    word = "intento" if attempts == 1 else "intentos"
                    return None, f"página {page}{of}: {type(e).__name__}: {e} (tras {attempts} {word})"
                delay = RETRY_BACKOFF_S * 2 ** (attempts - 1)
                logger.warning(f"[donpiso] Página {page}: {type(e).__name__}: {e}; "
                               f"reintento {attempts}/{MAX_RETRIES} en {delay:g}s")
                time.sleep(delay)

    def _parse_html(self, html: str) -> list[RawListing]:
        return self._parse_soup(BeautifulSoup(html, "html.parser"))

    def _parse_soup(self, soup: BeautifulSoup) -> list[RawListing]:

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
