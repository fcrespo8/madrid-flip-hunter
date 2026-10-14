"""
WallapopScraper — es.wallapop.com (la web llama a api.wallapop.com)

Un navegador headless carga la búsqueda y deja pasar a CloudFront; de ahí sale la primera
respuesta de /api/v3/search/section. Las páginas siguientes se piden con fetch() desde esa
misma página (misma huella de red que la app) con ?next_page=<meta.next_page de la anterior>.

Verificado contra el sitio (2026-10):
  - CloudFront responde 403 ("Request blocked") a toda request cuyo header User-Agent diga
    "HeadlessChrome", aunque el stealth cambie navigator.userAgent en JavaScript: ese header
    sale antes de que corra JS. Por eso el contexto fija un user_agent normal.
  - La página 2 es GET /api/v3/search/section?next_page=<token>, con los headers de la app
    (x-deviceid, mpid, x-appversion, deviceos...) que se copian de la primera llamada.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .base_scraper import (
    BAD_PAGINATION, BLOCKED, HTTP_ERROR, NO_API, PAGE_CAP, REPEATED_PAGE, UNPARSEABLE,
    BaseScraper, RawListing, ScrapeResult,
)

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
API_URL = "https://api.wallapop.com/api/v3/search/section"
# Headers de la primera llamada de la app que se reenvían en las páginas siguientes.
API_HEADERS = ("accept", "accept-language", "deviceos", "mpid", "trackinguserid",
               "x-appversion", "x-deviceid", "x-deviceos")

PAGE_DELAY_S = 1.0         # pausa entre páginas
MAX_RETRIES = 2            # reintentos por página tras el primer intento (hasta 3 requests)
RETRY_BACKOFF_S = 2.0      # espera antes de cada reintento: 2 s, 4 s, ...
FIRST_API_WAIT_S = 10      # cuánto esperar la primera llamada a search/section tras cargar la página
FIRST_API_POLL_S = 0.5
# Cinturón de seguridad ante una API que nunca termina; NO es un tope de scraping
# (el recorrido sigue hasta que la API deja de devolver meta.next_page).
MAX_PAGES_SAFETY = 200

BLOCK_STATUSES = {401, 403, 429}

_FETCH_JS = """async ({url, headers}) => {
    const r = await fetch(url, {headers});
    return {status: r.status, text: await r.text()};
}"""


def is_retryable_status(status: int) -> bool:
    """Transitorio: timeout, rate limit o error del servidor. Un 403 no se arregla esperando."""
    return status in (408, 429) or status >= 500


def failure_reason(status: Optional[int]) -> str:
    """Motivo a registrar cuando una respuesta falla: bloqueo (401/403/429) o error HTTP."""
    return BLOCKED if status in BLOCK_STATUSES else HTTP_ERROR


@dataclass
class SectionPage:
    """Lo que interesa de una respuesta de search/section."""
    items: Optional[list]          # None si el formato no es el esperado
    next_page: Optional[str]       # token de la página siguiente; None si es la última
    has_meta: bool                 # la respuesta trae el bloque `meta`


def parse_section(body) -> SectionPage:
    if not isinstance(body, dict):
        return SectionPage(None, None, False)
    data = body.get("data")
    section = data.get("section") if isinstance(data, dict) else None
    items = section.get("items") if isinstance(section, dict) else None
    meta = body.get("meta")
    token = meta.get("next_page") if isinstance(meta, dict) else None
    return SectionPage(
        items=items if isinstance(items, list) else None,
        next_page=token if isinstance(token, str) and token else None,
        has_meta=isinstance(meta, dict),
    )


class WallapopScraper(BaseScraper):

    SEARCH_URL = (
        "https://es.wallapop.com/app/search"
        "?keywords=piso+en+venta+madrid"
        "&category_ids=200"
        "&longitude=-3.7037902"
        "&latitude=40.4167754"
        "&distance=15000"
    )

    def __init__(self):
        super().__init__(source_name="wallapop")

    async def fetch_listings(self) -> ScrapeResult:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                # user_agent explícito: sin él, el header dice "HeadlessChrome" y CloudFront devuelve 403.
                context = await browser.new_context(locale="es-ES", user_agent=USER_AGENT)
                page = await context.new_page()
                await Stealth().apply_stealth_async(page)
                return await self._crawl(page)
            finally:
                await browser.close()

    async def _crawl(self, page) -> ScrapeResult:
        first: dict = {}

        async def capture(response):
            if "search/section" not in response.url or first.get("seen"):
                return
            first["seen"] = True
            first["status"] = response.status
            try:
                first["headers"] = await response.request.all_headers()
            except Exception:
                first["headers"] = {}
            try:
                first["body"] = await response.json()
            except Exception:
                first["body"] = None
            first["done"] = True

        page.on("response", capture)

        status, problem, reason = await self._navigate(page)
        if problem:
            logger.error(f"[wallapop] {problem}")
            return ScrapeResult.finish([], reached_end=False, error=problem, error_reason=reason)

        for _ in range(int(FIRST_API_WAIT_S / FIRST_API_POLL_S)):
            if first.get("done"):
                break
            await asyncio.sleep(FIRST_API_POLL_S)
        if not first.get("done"):
            title = await self._title(page)
            msg = f"la página cargó (HTTP {status}, {title!r}) pero no hubo llamada a search/section"
            logger.error(f"[wallapop] {msg}")
            return ScrapeResult.finish([], reached_end=False, error=msg, error_reason=NO_API)
        if first["status"] != 200:
            msg = f"search/section respondió HTTP {first['status']} en la primera llamada"
            logger.error(f"[wallapop] {msg}")
            return ScrapeResult.finish([], reached_end=False, error=msg, error_reason=failure_reason(first["status"]))

        headers = {k: v for k, v in first["headers"].items() if k in API_HEADERS}
        section = parse_section(first["body"])

        listings = []
        site_ids = set()   # ids que mostró la API
        # Si el for termina sin break, se llegó al cinturón de seguridad y quedaba catálogo.
        reached_end, stop_reason, error, error_reason = False, PAGE_CAP, None, HTTP_ERROR

        for number in range(1, MAX_PAGES_SAFETY + 1):
            if section.items is None:
                logger.error(f"[wallapop] Página {number}: formato de respuesta inesperado, parando.")
                stop_reason = UNPARSEABLE
                break

            ids = {i["id"] for i in section.items if isinstance(i, dict) and i.get("id")}
            new_ids = ids - site_ids
            if not new_ids and section.next_page:
                logger.warning(f"[wallapop] Página {number} sin anuncios nuevos, parando.")
                stop_reason = REPEATED_PAGE
                break
            site_ids |= ids

            seen = {x.external_id for x in listings}
            for item in section.items:
                listing = self._parse_item(item) if isinstance(item, dict) else None
                if listing and listing.external_id not in seen:
                    seen.add(listing.external_id)
                    listings.append(listing)
            logger.info(f"[wallapop] Página {number}: {len(section.items)} anuncios, {len(site_ids)} únicos hasta ahora")

            if not section.has_meta:
                # Sin `meta` no se puede saber si hay más: no se asume que terminó.
                logger.error(f"[wallapop] Página {number} sin bloque meta, parando.")
                stop_reason = BAD_PAGINATION
                break
            if not section.next_page:
                reached_end = True
                break
            if number == MAX_PAGES_SAFETY:
                break   # quedaba catálogo y se llegó al techo: no pedir una página que no se va a procesar

            await asyncio.sleep(PAGE_DELAY_S)
            body, error, error_reason = await self._fetch_next(page, headers, section.next_page, number + 1)
            if error:
                logger.error(f"[wallapop] {error}")
                break
            section = parse_section(body)

        # La API no informa el total de resultados: sin total_reported.
        return ScrapeResult.finish(
            listings, reached_end=reached_end, stop_reason=stop_reason,
            total_reported=None, items_seen=len(site_ids), error=error, error_reason=error_reason,
        )

    async def _title(self, page) -> str:
        try:
            return await page.title()
        except Exception:
            return ""

    async def _navigate(self, page) -> tuple[Optional[int], Optional[str], str]:
        """Carga la búsqueda con reintentos ante fallos transitorios. Devuelve (status, None, "")
        si cargó, o (status, descripción del problema, motivo) si no. Un 403 no se reintenta."""
        attempts = 0
        while True:
            attempts += 1
            status, problem = None, None
            try:
                resp = await page.goto(self.SEARCH_URL, wait_until="networkidle")
                status = resp.status if resp else None
                if status is not None and status >= 400:
                    server = (resp.headers or {}).get("server", "?")
                    problem = f"HTTP {status} en la primera respuesta (server: {server}, {await self._title(page)!r})"
            except Exception as e:
                problem = f"navegación fallida: {type(e).__name__}: {str(e)[:150]}"
            if problem is None:
                return status, None, ""
            retryable = status is None or is_retryable_status(status)
            if attempts > MAX_RETRIES or not retryable:
                word = "intento" if attempts == 1 else "intentos"
                return status, f"{problem} (tras {attempts} {word})", failure_reason(status)
            delay = RETRY_BACKOFF_S * 2 ** (attempts - 1)
            logger.warning(f"[wallapop] {problem}; reintento {attempts}/{MAX_RETRIES} en {delay:g}s")
            await asyncio.sleep(delay)

    async def _fetch_next(self, page, headers: dict, token: str, number: int):
        """Pide la página siguiente con fetch() dentro de la página, con reintentos y backoff.
        Devuelve (cuerpo, None, "") o (None, error, motivo)."""
        url = f"{API_URL}?next_page={quote(token, safe='')}"
        attempts = 0
        while True:
            attempts += 1
            status, problem = None, None
            try:
                r = await page.evaluate(_FETCH_JS, {"url": url, "headers": headers})
                status = r["status"]
                if status != 200:
                    problem = f"HTTP {status}"
                else:
                    try:
                        return json.loads(r["text"]), None, ""
                    except ValueError:
                        problem = "la respuesta no es JSON"
            except Exception as e:
                problem = f"{type(e).__name__}: {str(e)[:150]}"
            retryable = status is None or status == 200 or is_retryable_status(status)
            if attempts > MAX_RETRIES or not retryable:
                word = "intento" if attempts == 1 else "intentos"
                return None, f"página {number}: {problem} (tras {attempts} {word})", failure_reason(status)
            delay = RETRY_BACKOFF_S * 2 ** (attempts - 1)
            logger.warning(f"[wallapop] Página {number}: {problem}; reintento {attempts}/{MAX_RETRIES} en {delay:g}s")
            await asyncio.sleep(delay)

    def _parse_item(self, item: dict) -> RawListing | None:
        item_id = item.get("id")
        if not item_id:
            return None

        web_slug = item.get("web_slug", "")
        url = f"https://es.wallapop.com/item/{web_slug}" if web_slug else f"https://es.wallapop.com/item/{item_id}"

        title = item.get("title", "Sin título")
        description = item.get("description", "")

        price_data = item.get("price", {})
        price = price_data.get("amount")

        location = item.get("location", {})
        lat = location.get("latitude")
        lon = location.get("longitude")

        type_attrs = item.get("type_attributes", {})
        rooms = type_attrs.get("rooms")

        neighborhood = None
        match = re.search(r"Piso en (?:venta|alquiler) en (.+?) en Madrid", title, re.IGNORECASE)
        if match:
            neighborhood = match.group(1).strip()

        return RawListing(
            source="wallapop",
            external_id=item_id,
            url=url,
            title=title,
            price=price,
            size_m2=None,
            rooms=rooms,
            neighborhood=neighborhood,
            district=None,
            lat=lat,
            lon=lon,
            description=description,
        )
