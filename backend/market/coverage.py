"""Cobertura del resolvedor de zonas sobre una lista de anuncios (puro: no toca la base)."""
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from backend.market.resolver import ZoneResolver
from backend.market.zones import normalize_zone_name


@dataclass(frozen=True)
class ListingLocation:
    id: int
    source: str
    neighborhood: Optional[str]
    district: Optional[str]


def has_location(neighborhood: Optional[str], district: Optional[str]) -> bool:
    return any(isinstance(t, str) and normalize_zone_name(t) for t in (neighborhood, district))


@dataclass
class CoverageReport:
    total: int = 0                       # anuncios analizados
    with_location: int = 0               # traen barrio o distrito
    matched: int = 0                     # resolvieron a una zona
    out_of_scope: int = 0                # texto de otro municipio (reconocido, pero sin zona)
    unresolved: int = 0                  # con ubicación y sin zona ni alias
    by_level: Counter = field(default_factory=Counter)
    by_source: dict = field(default_factory=dict)             # fuente → Counter(matched/out_of_scope/unresolved)
    unresolved_texts: Counter = field(default_factory=Counter)     # (barrio, distrito) → anuncios
    unresolved_examples: dict = field(default_factory=dict)        # (barrio, distrito) → (id, fuente)

    @property
    def without_location(self) -> int:
        return self.total - self.with_location

    @property
    def zone_coverage(self) -> float:
        """% de los anuncios con ubicación que terminan con una zona."""
        return 100 * self.matched / self.with_location if self.with_location else 0.0

    @property
    def recognized_coverage(self) -> float:
        """% de los anuncios con ubicación que se reconocen: con zona o de otro municipio."""
        return 100 * (self.matched + self.out_of_scope) / self.with_location if self.with_location else 0.0


def coverage_report(resolver: ZoneResolver, listings: Iterable[ListingLocation]) -> CoverageReport:
    report = CoverageReport()
    for item in listings:
        report.total += 1
        if not has_location(item.neighborhood, item.district):
            continue
        report.with_location += 1
        match = resolver.resolve(item.neighborhood, item.district)
        bucket = report.by_source.setdefault(item.source, Counter())
        if match.matched:
            report.matched += 1
            report.by_level[match.level] += 1
            bucket["matched"] += 1
        elif match.unscorable_reason == "out_of_scope":
            report.out_of_scope += 1
            bucket["out_of_scope"] += 1
        else:
            report.unresolved += 1
            bucket["unresolved"] += 1
            key = (item.neighborhood, item.district)
            report.unresolved_texts[key] += 1
            report.unresolved_examples.setdefault(key, (item.id, item.source))
    return report
