"""Las fuentes que scrapea run_all, para quien necesita saber algo de cada una sin correrla."""
from .donpiso_scraper import DonpisoScraper
from .redpiso_scraper import RedpisoScraper
from .remax_scraper import RemaxScraper
from .tecnocasa_scraper import TecnocasaScraper
from .wallapop_scraper import WallapopScraper

SCRAPER_CLASSES = (WallapopScraper, DonpisoScraper, RemaxScraper, RedpisoScraper, TecnocasaScraper)


def sources_without_size_enrichment() -> frozenset[str]:
    """Fuentes con `supports_size_enrichment = False`: enrich_sizes no baja sus páginas."""
    return frozenset(s.source_name for s in (cls() for cls in SCRAPER_CLASSES)
                     if not s.supports_size_enrichment)
