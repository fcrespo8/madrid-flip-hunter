from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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


class BaseScraper(ABC):

    def __init__(self, source_name: str):
        self.source_name = source_name

    @abstractmethod
    async def fetch_listings(self) -> list[RawListing]:
        """Cada scraper implementa su propia lógica de extracción."""
        pass

    async def run(self) -> list[RawListing]:
        print(f"[{self.source_name}] Iniciando scraping...")
        listings = await self.fetch_listings()
        print(f"[{self.source_name}] {len(listings)} listings encontrados.")
        return listings
