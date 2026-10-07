from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Un barrido es "completo" si vio al menos esta fracción de lo que el sitio dice tener.
COVERAGE_MIN = 0.9

# Por qué un barrido no es completo (ScrapeResult.incomplete_reason).
ERROR = "error"                    # falló la descarga o el scraper tiró una excepción
EMPTY = "empty"                    # no trajo ningún listing (p. ej. bloqueado)
PAGE_CAP = "page_cap"              # cortó por el tope de páginas, quedaba catálogo
REPEATED_PAGE = "repeated_page"    # una página no trajo nada nuevo (el sitio ignora el parámetro de página)
NO_PAGINATION = "no_pagination"    # el scraper no recorre más allá de la primera página
UNPARSEABLE = "unparseable_page"   # la página tenía ítems pero ninguno se pudo leer
LOW_COVERAGE = "low_coverage"      # vio menos del COVERAGE_MIN de lo que el sitio informa


@dataclass
class RawListing:
    source: str
    external_id: str
    url: str
    title: str
    price: Optional[float]
    size_m2: Optional[float]
    rooms: Optional[int]
    neighborhood: Optional[str]
    district: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    description: Optional[str]
    scraped_at: datetime = None

    def __post_init__(self):
        if self.scraped_at is None:
            self.scraped_at = datetime.utcnow()


def as_count(value) -> Optional[int]:
    """Total informado por un sitio ("1.342", 926, "142") → int; None si no es un número."""
    try:
        n = int(str(value).replace(".", "").strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


@dataclass
class ScrapeResult:
    """Lo que devuelve un scraper: los listings y si el barrido fue exhaustivo.

    `complete=True` significa "esto es TODO lo que la fuente tiene publicado". Solo
    entonces se puede inferir que lo que no apareció ya no está publicado (ver
    deactivate_stale). Se arma siempre con `finish()`, que concentra la regla."""
    listings: list[RawListing] = field(default_factory=list)
    complete: bool = False
    total_reported: Optional[int] = None     # total que informa el sitio (None si no lo muestra)
    items_seen: int = 0                      # ítems que el sitio mostró, antes de nuestros filtros
    incomplete_reason: Optional[str] = None  # None si complete
    error: Optional[str] = None

    @classmethod
    def finish(
        cls,
        listings: list[RawListing],
        *,
        reached_end: bool,
        stop_reason: Optional[str] = None,
        total_reported: Optional[int] = None,
        items_seen: Optional[int] = None,
        error: Optional[str] = None,
    ) -> "ScrapeResult":
        """Cierra un barrido y decide `complete`.

        reached_end: el scraper llegó al final natural del catálogo (última página o
        página vacía); no cuenta si cortó por un tope, una página repetida o un error.
        items_seen: ítems que mostró el sitio, incluidos los que el scraper descarta
        por diseño (otros municipios, sin precio): es lo que se compara con el total.
        Precedencia de motivos: error > no llegó al final > vacío > cobertura baja."""
        unique = list({x.external_id: x for x in listings}.values())
        seen = max(items_seen if items_seen is not None else 0, len(unique))

        reason = None
        if error:
            reason = ERROR
        elif not reached_end:
            reason = stop_reason or PAGE_CAP
        elif not unique:
            reason = EMPTY
        elif total_reported and seen < COVERAGE_MIN * total_reported:
            reason = LOW_COVERAGE

        return cls(listings=unique, complete=reason is None, total_reported=total_reported,
                   items_seen=seen, incomplete_reason=reason, error=error)

    @classmethod
    def failed(cls, error: str) -> "ScrapeResult":
        return cls.finish([], reached_end=False, error=error)


class PageWalk:
    """Acumula lo que ve un scraper que pagina y detecta páginas repetidas."""

    def __init__(self):
        self.listings: list[RawListing] = []
        self.items_seen = 0
        self._ids: set[str] = set()

    def add_page(self, page_listings: list[RawListing], site_items: Optional[int] = None) -> bool:
        """Suma una página. Devuelve False si no trajo ningún listing nuevo: el sitio
        está devolviendo la misma página (ignora el parámetro) y hay que cortar.
        site_items: ítems que mostraba la página antes de filtrar (default: los parseados)."""
        new = [x for x in page_listings if x.external_id not in self._ids]
        if not new:
            return False
        self._ids.update(x.external_id for x in new)
        self.listings.extend(new)
        self.items_seen += site_items if site_items is not None else len(page_listings)
        return True


class BaseScraper(ABC):

    def __init__(self, source_name: str):
        self.source_name = source_name

    @abstractmethod
    async def fetch_listings(self) -> ScrapeResult:
        """Cada scraper implementa su propia lógica de extracción y dice si fue exhaustivo."""
        pass

    async def run(self) -> ScrapeResult:
        print(f"[{self.source_name}] Iniciando scraping...")
        result = await self.fetch_listings()
        total = f" de {result.total_reported}" if result.total_reported else ""
        status = "completo" if result.complete else f"incompleto ({result.incomplete_reason})"
        print(f"[{self.source_name}] {len(result.listings)}{total} listings encontrados, barrido {status}.")
        return result

    async def scrape(self) -> ScrapeResult:
        return await self.run()
