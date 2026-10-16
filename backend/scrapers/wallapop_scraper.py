"""
WallapopScraper — es.wallapop.com (la web llama a api.wallapop.com)

Un navegador headless carga la búsqueda y deja pasar a CloudFront; de ahí sale la primera
respuesta de /api/v3/search/section. Las páginas siguientes se piden con fetch() desde esa
misma página (misma huella de red que la app) con ?next_page=<meta.next_page de la anterior>.

Verificado contra el sitio (2026-10):
  - CloudFront responde 403 ("Request blocked") a toda request cuyo header User-Agent diga
    "HeadlessChrome", aunque el stealth cambie navigator.userAgent en JavaScript: ese header
    sale antes de que corra JS. Por eso el contexto fija un user_agent normal.
  - La app IGNORA latitude/longitude de la URL de búsqueda (con Madrid o con Barcelona pide a la API
    40.4252/-3.6927): las toma de otro lado (default, geolocalización de la IP...). Por eso el
    pedido a search/section se reescribe con page.route para fijar las coordenadas de Madrid.
  - La página 2 es GET /api/v3/search/section?next_page=<token>, con los headers de la app
    (x-deviceid, mpid, x-appversion, deviceos...) que se copian de la primera llamada.
"""
import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .base_scraper import (
    BAD_PAGINATION, BLOCKED, EMPTY_FIRST_PAGE, HTTP_ERROR, NO_API, PAGE_CAP, REPEATED_PAGE, UNPARSEABLE,
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

# Centro de Madrid: la búsqueda no debe depender de dónde crea la app que está el navegador.
MADRID_LAT, MADRID_LON = 40.4167754, -3.7037902
SECTION_ROUTE = "**/api/v3/search/section*"

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


def pin_coordinates(url: str, lat: float = MADRID_LAT, lon: float = MADRID_LON) -> str:
    """URL de search/section con latitude/longitude fijadas; el resto del pedido queda igual."""
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["latitude"], query["longitude"] = [str(lat)], [str(lon)]
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))


def request_coordinates(url: str) -> tuple[Optional[str], Optional[str]]:
    """(latitude, longitude) con que se hizo un pedido a search/section; None si no las trae."""
    query = parse_qs(urlparse(url).query)
    return (query.get("latitude") or [None])[0], (query.get("longitude") or [None])[0]


def describe_first_response(status, url: str, body, section: "SectionPage") -> str:
    """Detalle de la primera respuesta de la API para el log: es lo único que permite distinguir
    un bloqueo suave (200 con lista vacía) de un cambio de formato."""
    lat, lon = request_coordinates(url or "")
    items = section.items
    with_id = sum(1 for i in items if isinstance(i, dict) and i.get("id")) if items is not None else None
    meta = body.get("meta") if isinstance(body, dict) else None
    return (f"HTTP {status}, ítems={None if items is None else len(items)} (con id={with_id}), "
            f"lat={lat} lon={lon}, claves de meta={sorted(meta) if isinstance(meta, dict) else None}, "
            f"next_page={'sí' if section.next_page else 'no'}, "
            f"claves de body={sorted(body) if isinstance(body, dict) else type(body).__name__}")


def is_retryable_status(status: int) -> bool:
    """Transitorio: timeout, rate limit o error del servidor. Un 403 no se arregla esperando."""
    return status in (408, 429) or status >= 500


def failure_reason(status: Optional[int]) -> str:
    """Motivo a registrar cuando una respuesta falla: bloqueo (401/403/429) o error HTTP."""
    return BLOCKED if status in BLOCK_STATUSES else HTTP_ERROR


def parse_surface(value) -> Optional[float]:
    """type_attributes.surface → m². None si falta o no es un número finito y positivo (un 0 o un
    texto raro como tamaño haría que el QA rechace el listing en vez de dejarlo sin dato)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.replace(",", "."))
        except ValueError:
            return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value)
    return None


# Superficie escrita en la descripción: "54 m²", "62m2", "80 metros cuadrados", "54,5 m²"...
# "metros" suelto NO cuenta ("a 300 metros del metro" es una distancia).
_AREA_RE = re.compile(
    r"(?<![\d.,])(\d{1,4}(?:[.,]\d{1,3})?)\s*(?:m²|m2|mt2|mts2|mt²|mts²|metros?\s+cuadrados?|m\s+cuadrados?)",
    re.IGNORECASE,
)
# Espacios que NO son la vivienda ("terraza de 20 m²", "salón comedor de 30 m²", "12 m² de terraza").
_NON_DWELLING = (r"terraza|balc[oó]n|patio|jard[ií]n|parcela|solar|sol[aá]rium|trastero|garaje|parking|plaza de|local|"
                 r"cocina|sal[oó]n|comedor|dormitorio|habitaci[oó]n|ba[nñ]o|azotea|s[oó]tano|buhardilla|porche|"
                 r"zonas? comunes?|piscina|cuarto|vestidor|despacho|entreplanta|altillo|bodega|desv[aá]n")
# Nombra la vivienda principal: si aparece entre la palabra clave y el número, el número es de la vivienda.
_MAIN_DWELLING = r"piso|vivienda|casa|apartamento|inmueble|[aá]tico|d[uú]plex|chalet"
# Palabra clave en la misma oración, pocos caracteres antes del número y sin nombrar la vivienda de por medio.
_BEFORE_NON_DWELLING = re.compile(
    rf"(?:{_NON_DWELLING})(?:(?!\b(?:{_MAIN_DWELLING})\b)[^.;\n]){{0,22}}$", re.IGNORECASE)
_AFTER_NON_DWELLING = re.compile(rf"^\s*(?:de|en|para)\s+(?:{_NON_DWELLING})", re.IGNORECASE)   # "[12 m²] de terraza"
MIN_M2, MAX_M2 = 15, 1000      # mismo rango plausible que usa el QA (SIZE_MIN / SIZE_MAX)


def parse_size_from_description(text) -> Optional[float]:
    """Primer m² de la descripción que sea plausible (15–1.000) y no sea de un espacio accesorio
    (terraza, salón, trastero...). None si no hay. Medido contra `surface` en ~270 anuncios reales
    que traen ambos: ~89 % queda entre 0,97x y 1,35x. Los demás casos son, sobre todo, superficie
    CONSTRUIDA contra la ÚTIL de `surface` (las descripciones suelen citar primero la construida)."""
    if not isinstance(text, str):
        return None
    for m in _AREA_RE.finditer(text):
        raw = m.group(1)
        value = float(raw.replace(".", "")) if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw) else float(raw.replace(",", "."))
        if not MIN_M2 <= value <= MAX_M2:
            continue
        before, after = text[max(0, m.start() - 45):m.start()], text[m.end():m.end() + 30]
        if _BEFORE_NON_DWELLING.search(before) or _AFTER_NON_DWELLING.match(after):
            continue
        return value
    return None


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

    # Wallapop NO es un catálogo: es una búsqueda por palabras clave ordenada por relevancia
    # (~2.270 resultados en 61 páginas). Que un anuncio no aparezca en los resultados no prueba
    # que se haya despublicado: puede haber salido del ranking o dejar de coincidir con la
    # búsqueda. Por eso un barrido completo (complete=True) no se usa para desactivar listings.
    supports_deactivation = False

    # Medido (2026-10): bajar la página de un anuncio (es.wallapop.com/item/...) no da los m² en 0 de
    # 10 casos, y cada intento cuesta ~6 s de navegador. Los m² de Wallapop salen de `surface` y
    # de la descripción al scrapear; enrich_sizes no aporta nada y se come el tope de 50 descargas.
    supports_size_enrichment = False

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
            first["url"] = response.url
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

        async def pin(route):
            """Fija las coordenadas de Madrid en el pedido de la app (ver docstring del módulo)."""
            try:
                await route.continue_(url=pin_coordinates(route.request.url))
            except Exception:
                await route.continue_()      # nunca dejar colgado un pedido por esto

        await page.route(SECTION_ROUTE, pin)

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
        logger.info(f"[wallapop] Primera respuesta: {describe_first_response(first['status'], first.get('url'), first['body'], section)}")

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
            if number == 1 and not ids:
                # 200 con la primera página vacía (o sin ids): no es el fin del catálogo ni una página repetida.
                logger.error("[wallapop] Primera página sin anuncios: "
                             + describe_first_response(first["status"], first.get("url"), first["body"], section))
                stop_reason = EMPTY_FIRST_PAGE
                break
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

        type_attrs = item.get("type_attributes") or {}
        rooms = type_attrs.get("rooms")
        # `surface` (estructurado) viene en ~42 % de los anuncios. Si falta, se lee de la descripción
        # (cobertura total ~75 %); lo que siga sin dato lo intenta enrich_sizes.
        size_m2 = parse_surface(type_attrs.get("surface"))
        if size_m2 is None:
            size_m2 = parse_size_from_description(description)

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
            size_m2=size_m2,
            rooms=rooms,
            neighborhood=neighborhood,
            district=None,
            lat=lat,
            lon=lon,
            description=description,
        )
