"""Zonas oficiales de Madrid a partir del CSV registral, y su siembra en la base.

`normalize_zone_name` y `zone_specs_from_rows` son puras. `seed_zones` escribe en la sesión que le
pasen (hace flush, nunca commit): quien la llama decide si confirma o descarta. Es idempotente.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional

from sqlalchemy.orm import Session

from backend.market.registral import CITY_NAME, RegistralRow
from backend.models.zone import Zone, ZoneAlias

if TYPE_CHECKING:
    from backend.market.aliases import AliasSpec

ZoneKey = tuple[str, Optional[str]]          # (level, code); la ciudad es ("ciudad", None)
CITY_KEY: ZoneKey = ("ciudad", None)


class ZoneSeedError(ValueError):
    """Los datos de origen o la base no permiten sembrar sin ambigüedad."""


def normalize_zone_name(name: str) -> str:
    """'Villaverde Alto, Casco Histórico' → 'villaverde alto casco historico'. Minúsculas, sin tildes
    (ni ñ ni ü), y todo lo que no sea letra o dígito pasa a un espacio ('Moncloa-Aravaca' → 'moncloa aravaca')."""
    decomposed = unicodedata.normalize("NFKD", name.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


@dataclass(frozen=True)
class ZoneSpec:
    level: str
    code: Optional[str]
    name: str
    name_norm: str
    parent: Optional[ZoneKey]

    @property
    def key(self) -> ZoneKey:
        return (self.level, self.code)


def zone_specs_from_rows(rows: list[RegistralRow]) -> list[ZoneSpec]:
    """Zonas distintas del CSV: ciudad, distritos y barrios, en ese orden (cada padre antes que sus hijos).
    Falla si un código aparece con dos nombres distintos o si dos zonas comparten nombre normalizado."""
    names: dict[ZoneKey, str] = {}
    parents: dict[ZoneKey, Optional[ZoneKey]] = {}
    for r in rows:
        if r.level == "ciudad":
            key, name, parent = CITY_KEY, CITY_NAME, None
        elif r.level == "distrito":
            key, name, parent = ("distrito", r.district_code), r.district_name, CITY_KEY
        else:
            key, name, parent = ("barrio", r.barrio_code), r.barrio_name, ("distrito", r.district_code)
        if names.setdefault(key, name) != name:
            raise ZoneSeedError(f"el código {key} aparece con dos nombres: {names[key]!r} y {name!r}")
        if parents.setdefault(key, parent) != parent:
            raise ZoneSeedError(f"el código {key} aparece con dos padres distintos")

    order = {"ciudad": 0, "distrito": 1, "barrio": 2}
    specs = [ZoneSpec(key[0], key[1], name, normalize_zone_name(name), parents[key])
             for key, name in sorted(names.items(), key=lambda kv: (order[kv[0][0]], kv[0][1] or ""))]

    seen: dict[str, ZoneSpec] = {}
    for spec in specs:
        if spec.name_norm in seen:
            raise ZoneSeedError(f"{spec.name!r} y {seen[spec.name_norm].name!r} tienen el mismo nombre normalizado")
        seen[spec.name_norm] = spec
        if spec.parent is not None and spec.parent not in {s.key for s in specs}:
            raise ZoneSeedError(f"{spec.name!r}: su padre {spec.parent} no está en el archivo")
    return specs


@dataclass
class SeedResult:
    zones_created: int = 0
    zones_updated: int = 0
    zones_unchanged: int = 0
    aliases_created: int = 0
    aliases_updated: int = 0
    manual_created: int = 0
    manual_updated: int = 0
    zones_not_in_source: int = 0
    manual_not_in_source: int = 0      # alias manuales de la base que ya no están en el archivo (no se borran)

    @property
    def changed(self) -> bool:
        return bool(self.zones_created or self.zones_updated or self.aliases_created or self.aliases_updated
                    or self.manual_created or self.manual_updated)


def seed_zones(db: Session, specs: list[ZoneSpec], manual_aliases: Iterable["AliasSpec"] = ()) -> SeedResult:
    """Crea las zonas que faltan, corrige las que cambiaron de nombre o de padre, y se asegura de que
    cada una tenga su nombre oficial como alias ('official'). Además carga los alias de `manual_aliases`
    (data/aliases.csv) como 'manual': el archivo manda sobre un alias manual que ya esté en la base, pero
    nunca pisa un alias oficial. No borra nada. Correrla de nuevo no cambia nada. Hace flush, no commit."""
    result = SeedResult()
    existing: dict[ZoneKey, Zone] = {(z.level, z.code): z for z in db.query(Zone).all()}
    in_source = {s.key for s in specs}
    result.zones_not_in_source = sum(1 for key in existing if key not in in_source)

    for spec in specs:             # padres antes que hijos (zone_specs_from_rows ya los ordena)
        parent_id = existing[spec.parent].id if spec.parent is not None else None
        zone = existing.get(spec.key)
        if zone is None:
            zone = Zone(level=spec.level, code=spec.code, name=spec.name, name_norm=spec.name_norm, parent_id=parent_id)
            db.add(zone)
            db.flush()
            existing[spec.key] = zone
            result.zones_created += 1
        elif (zone.name, zone.name_norm, zone.parent_id) != (spec.name, spec.name_norm, parent_id):
            zone.name, zone.name_norm, zone.parent_id = spec.name, spec.name_norm, parent_id
            result.zones_updated += 1
        else:
            result.zones_unchanged += 1

    db.flush()
    aliases = {a.alias_norm: a for a in db.query(ZoneAlias).all()}
    for spec in specs:
        zone = existing[spec.key]
        alias = aliases.get(spec.name_norm)
        if alias is None:
            alias = ZoneAlias(alias_norm=spec.name_norm, zone_id=zone.id, kind=spec.level, source="official")
            db.add(alias)
            aliases[spec.name_norm] = alias
            result.aliases_created += 1
        elif alias.zone_id != zone.id:
            raise ZoneSeedError(
                f"el alias {spec.name_norm!r} ya apunta a otra zona (id {alias.zone_id}); no se pisa. "
                f"Revisá zone_aliases antes de sembrar {spec.name!r}.")
        elif (alias.kind, alias.source) != (spec.level, "official") and alias.source == "official":
            alias.kind = spec.level
            result.aliases_updated += 1
    manual_aliases = list(manual_aliases)
    in_file = {a.alias_norm for a in manual_aliases}
    result.manual_not_in_source = sum(1 for n, a in aliases.items() if a.source == "manual" and n not in in_file)
    for spec in manual_aliases:
        zone = existing.get(spec.target) if spec.target is not None else None
        if spec.target is not None and zone is None:
            raise ZoneSeedError(f"el alias manual {spec.text!r} apunta a {spec.target}, que no está entre las zonas sembradas")
        kind = zone.level if zone is not None else "out_of_scope"
        zone_id = zone.id if zone is not None else None
        alias = aliases.get(spec.alias_norm)
        if alias is None:
            alias = ZoneAlias(alias_norm=spec.alias_norm, zone_id=zone_id, kind=kind, source="manual", note=spec.note)
            db.add(alias)
            aliases[spec.alias_norm] = alias
            result.manual_created += 1
        elif alias.source == "official":
            raise ZoneSeedError(f"el alias manual {spec.text!r} choca con un alias oficial (zona id {alias.zone_id}); no se pisa.")
        elif (alias.zone_id, alias.kind, alias.note) != (zone_id, kind, spec.note):
            alias.zone_id, alias.kind, alias.note = zone_id, kind, spec.note
            result.manual_updated += 1
    db.flush()
    return result
