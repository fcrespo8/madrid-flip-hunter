"""Resuelve el texto de ubicación de un anuncio (barrio, distrito) a una zona oficial.

Busca el texto normalizado en `zone_aliases`: primero el barrio del anuncio y después su distrito. Es una
búsqueda exacta: sin match por subcadena ni por palabra ("Calle de Ezequiel Solana" no es "Sol"). Lo que
antes resolvía la subcadena lo resuelve ahora un alias explícito (ver docs/market_prices_design.md).

Resultados posibles de `ZoneResolver.resolve`:
  - zona encontrada      → `matched`, con la zona y de qué campo salió;
  - texto de otro municipio (alias `out_of_scope`) → `unscorable_reason = "out_of_scope"`;
  - nada que resolver    → `unscorable_reason = "no_zone"` (`detail` dice si faltaba la ubicación o el alias).

El resolvedor se carga una vez (unas 150 zonas) y resuelve en memoria: no consulta la base por anuncio.
"""
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from backend.market.zones import normalize_zone_name
from backend.models.zone import Zone, ZoneAlias

NO_ZONE = "no_zone"
OUT_OF_SCOPE = "out_of_scope"
DETAIL_NO_LOCATION = "sin_ubicacion"      # el anuncio no trae barrio ni distrito
DETAIL_NO_ALIAS = "sin_alias"             # trae texto, pero ningún alias lo reconoce


@dataclass(frozen=True)
class ZoneInfo:
    id: int
    level: str
    code: Optional[str]
    name: str
    parent_id: Optional[int]


@dataclass(frozen=True)
class AliasInfo:
    alias_norm: str
    zone_id: Optional[int]
    kind: str


@dataclass(frozen=True)
class ZoneMatch:
    zone_id: Optional[int] = None
    level: Optional[str] = None              # 'ciudad' | 'distrito' | 'barrio'
    code: Optional[str] = None
    name: Optional[str] = None
    parent_id: Optional[int] = None
    matched_on: Optional[str] = None         # 'neighborhood' | 'district'
    alias: Optional[str] = None              # texto normalizado que matcheó
    unscorable_reason: Optional[str] = None  # None si hay zona
    detail: Optional[str] = None

    @property
    def matched(self) -> bool:
        return self.zone_id is not None


class ZoneResolver:
    def __init__(self, zones: Iterable[ZoneInfo], aliases: Iterable[AliasInfo]):
        self._zones = {z.id: z for z in zones}
        self._aliases = {a.alias_norm: a for a in aliases}

    def resolve(self, neighborhood: Optional[str], district: Optional[str] = None) -> ZoneMatch:
        seen_text = False
        for field, raw in (("neighborhood", neighborhood), ("district", district)):
            key = normalize_zone_name(raw) if isinstance(raw, str) else ""
            if not key:
                continue
            seen_text = True
            alias = self._aliases.get(key)
            if alias is None:
                continue
            if alias.kind == "out_of_scope":
                return ZoneMatch(matched_on=field, alias=key, unscorable_reason=OUT_OF_SCOPE)
            zone = self._zones.get(alias.zone_id)
            if zone is None:       # alias huérfano: no es una zona, se sigue buscando
                continue
            return ZoneMatch(zone_id=zone.id, level=zone.level, code=zone.code, name=zone.name,
                             parent_id=zone.parent_id, matched_on=field, alias=key)
        return ZoneMatch(unscorable_reason=NO_ZONE, detail=DETAIL_NO_ALIAS if seen_text else DETAIL_NO_LOCATION)


def load_resolver(db: Session) -> ZoneResolver:
    zones = [ZoneInfo(z.id, z.level, z.code, z.name, z.parent_id) for z in db.query(Zone).all()]
    aliases = [AliasInfo(a.alias_norm, a.zone_id, a.kind) for a in db.query(ZoneAlias).all()]
    return ZoneResolver(zones, aliases)


def resolve(db: Session, neighborhood: Optional[str], district: Optional[str] = None) -> ZoneMatch:
    """Atajo para un solo anuncio. Para un lote, cargá `load_resolver(db)` una vez."""
    return load_resolver(db).resolve(neighborhood, district)
