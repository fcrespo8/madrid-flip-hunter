import asyncio
import logging
import re
from typing import Awaitable, Callable

from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from sqlalchemy import and_, or_
from backend.agents.qa_agent import SIZE_MAX, SIZE_MIN
from backend.models.database import SessionLocal
from backend.models.listing import Listing

logger = logging.getLogger(__name__)

# Tope por corrida: cada página tarda varios segundos (networkidle + espera).
DEFAULT_MAX_FETCHES = 50

FetchHtml = Callable[[str], Awaitable[str | None]]

# Motivo final: la página se bajó bien y no da un tamaño usable. El listing queda
# 'unscorable' y size_enrichment_query (que solo busca 'no_size') ya no lo reintenta.
# Para forzar un reintento: volver score_status_reason a 'no_size'.
NO_SIZE_FINAL = "no_size_final"

EMPTY_COUNTS = {"sized": 0, "size_rejected": 0, "size_not_found": 0, "fetch_failed": 0, "unscorable_reset": 0}

# Ordered from most-specific to most-generic.
# Each pattern captures the numeric value in group 1.
_PATTERNS = [
    # JSON/data attributes: "surface":85, "surface_area":85, "meters":85
    r'"(?:surface_area|surface|metros_cuadrados|meters|floor_size|square_meters)"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
    # Structured label next to value: "85 m²" or "85m²" preceded by common attribute separators
    r'(?:superficie|tamaño|size|area|metros)[^\d]{0,20}(\d+(?:[.,]\d+)?)\s*m[²2]',
    # Plain "85 m²" / "85m²" / "85 m2" anywhere in the HTML
    r'(\d+(?:[.,]\d+)?)\s*m[²2]',
]


def _extract_from_html(html: str) -> float | None:
    for pattern in _PATTERNS:
        for match in re.finditer(pattern, html, re.IGNORECASE):
            raw = match.group(1).replace(",", ".")
            try:
                value = float(raw)
                # Sanity-check: plausible apartment size range
                if 10 <= value <= 1000:
                    return value
            except ValueError:
                continue
    return None


def apply_size(listing: Listing, size_m2: float) -> bool:
    """Guarda size_m2. Si el listing era 'unscorable' por falta de tamaño, lo vuelve
    a 'pending' para que el próximo run_all lo pase por QA y pre_score.
    Devuelve True si hubo ese cambio de estado. No hace commit."""
    listing.size_m2 = size_m2
    if listing.score_status == "unscorable" and listing.score_status_reason == "no_size":
        listing.score_status = "pending"
        listing.score_status_reason = None
        return True
    return False


def is_valid_size(size_m2: float) -> bool:
    """Misma regla que el QA (15–1.000 m²): el tamaño se completa después del QA,
    así que acá es el único control que tiene."""
    return SIZE_MIN <= size_m2 <= SIZE_MAX


def size_enrichment_query(db, pending_ids: list[int] | None = None):
    """Listings sin tamaño que vale la pena completar: activos, no rechazados por QA,
    y pendientes (de `pending_ids`, o todos si es None) o unscorable por no_size."""
    pending = Listing.score_status == "pending"
    if pending_ids is not None:
        pending = Listing.id.in_(pending_ids) if pending_ids else Listing.id.is_(None)
    return (
        db.query(Listing)
        .filter(
            Listing.size_m2.is_(None),
            Listing.is_active.is_(True),
            Listing.qa_rejected.is_(False),
            or_(pending, and_(Listing.score_status == "unscorable",
                              Listing.score_status_reason == "no_size")),
        )
        .order_by(Listing.id.desc())   # primero los más nuevos
    )


def mark_no_size_final(listing: Listing) -> None:
    listing.score_status = "unscorable"
    listing.score_status_reason = NO_SIZE_FINAL


async def enrich_listing_sizes(db, listings: list[Listing], fetch_html: FetchHtml) -> dict[str, int]:
    """Baja la página de cada listing, extrae el tamaño y lo guarda si es válido.

    - Página OK con tamaño válido → guarda (y unscorable/no_size → pending).
    - Página OK sin m², o con m² fuera de 15–1.000 → 'unscorable' / no_size_final:
      no se vuelve a intentar.
    - Descarga fallida (excepción o sin HTML) → no se toca: se reintenta otro día.
    Un error en un listing no frena al resto. Devuelve contadores."""
    counts = dict(EMPTY_COUNTS)
    for listing in listings:
        try:
            html = await fetch_html(listing.url)
        except Exception as e:
            counts["fetch_failed"] += 1
            logger.warning("[enrich_size] Descarga fallida %s: %s", listing.external_id, e)
            continue
        if not html:
            counts["fetch_failed"] += 1
            logger.warning("[enrich_size] Descarga fallida %s: página vacía", listing.external_id)
            continue

        try:
            size_m2 = _extract_from_html(html)
            if size_m2 is None:
                counts["size_not_found"] += 1
                mark_no_size_final(listing)
                logger.info("[enrich_size] ✗ %s: tamaño no encontrado (final)", listing.external_id)
            elif not is_valid_size(size_m2):
                counts["size_rejected"] += 1
                mark_no_size_final(listing)
                logger.info("[enrich_size] ✗ %s: %s m² fuera de %s–%s (final)",
                            listing.external_id, size_m2, SIZE_MIN, SIZE_MAX)
            else:
                reset = apply_size(listing, size_m2)
                counts["sized"] += 1
                counts["unscorable_reset"] += int(reset)
                logger.info("[enrich_size] ✓ %s: %s m²%s", listing.external_id, size_m2,
                            " (unscorable → pending)" if reset else "")
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("[enrich_size] Error guardando %s: %s", listing.external_id, e)
    return counts


class PlaywrightFetcher:
    """fetch_html con un solo navegador para todo el lote. Usar con `async with`."""

    async def __aenter__(self) -> "PlaywrightFetcher":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._browser.close()
        await self._pw.stop()

    async def __call__(self, url: str) -> str | None:
        context = await self._browser.new_context(locale="es-ES")
        try:
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            return await page.content()
        finally:
            await context.close()


async def enrich_sizes(pending_ids: list[int] | None = None, max_fetches: int | None = DEFAULT_MAX_FETCHES,
                       fetch_html: FetchHtml | None = None) -> dict[str, int]:
    """Completa size_m2 (regla 15–1.000 m²) y saca de 'unscorable' a los no_size.
    Abre y cierra su propia sesión. Sin fetch_html usa Playwright."""
    db = SessionLocal()
    try:
        query = size_enrichment_query(db, pending_ids)
        if max_fetches is not None:
            query = query.limit(max_fetches)
        listings = query.all()
        logger.info("[enrich_size] %d listings sin tamaño", len(listings))
        if not listings:
            return dict(EMPTY_COUNTS)
        if fetch_html is not None:
            return await enrich_listing_sizes(db, listings, fetch_html)
        async with PlaywrightFetcher() as fetcher:
            return await enrich_listing_sizes(db, listings, fetcher)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(asyncio.run(enrich_sizes(max_fetches=None)))
