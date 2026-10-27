"""Precio de referencia de una zona: el último precio disponible, y de qué nivel salió.

Un barrio usa su propio precio; si no lo tiene (menos de 15 compraventas, que el Ayuntamiento no publica) o
es de un período anterior al de su distrito, usa el del distrito. Un distrito o la ciudad usan el suyo.
Si dos cargas traen el mismo (zona, período), vale la más reciente (las anteriores se conservan).
"""
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Union

from sqlalchemy.orm import Session

from backend.market.importer import SOURCE_REGISTRAL_USADAS
from backend.market.rollforward import DEFAULT_SERIES, IndexNotAvailable, RollforwardFactor, rollforward_factor
from backend.models.market import MarketPrice
from backend.models.zone import Zone

REASON_NO_DATA = "sin_dato"                      # el barrio no tiene ningún precio
REASON_STALE = "barrio_desactualizado"           # el último precio del barrio es anterior al de su distrito


@dataclass(frozen=True)
class ReferencePrice:
    price_m2: float
    level: str                       # nivel de la zona de la que salió: 'barrio' | 'distrito' | 'ciudad'
    zone_id: int                     # la zona cuyo precio se usó (puede ser el distrito del barrio pedido)
    period: date                     # período del precio
    import_id: int
    fallback_reason: Optional[str] = None     # None si salió de la zona pedida
    # Solo con update_to: el precio actualizado con el IPV, y con qué factor. price_m2 sigue siendo el original.
    updated_price_m2: Optional[float] = None
    rollforward: Optional[RollforwardFactor] = None
    rollforward_error: Optional[str] = None   # por qué no se pudo actualizar (índice sin cargar, año incompleto...)

    @property
    def is_fallback(self) -> bool:
        return self.fallback_reason is not None


def _zone_id(zone) -> Optional[int]:
    """Acepta un id, una Zone (`.id`) o un ZoneMatch del resolvedor (`.zone_id`, None si no resolvió)."""
    if isinstance(zone, int) and not isinstance(zone, bool):
        return zone
    zid = getattr(zone, "zone_id", None)
    return zid if zid is not None else getattr(zone, "id", None)


def _latest(db: Session, zone_id: int, source: str) -> Optional[MarketPrice]:
    return (db.query(MarketPrice).filter_by(zone_id=zone_id, source=source)
            .order_by(MarketPrice.period.desc(), MarketPrice.import_id.desc()).first())


def _result(row: MarketPrice, level: str, reason: Optional[str] = None) -> ReferencePrice:
    return ReferencePrice(row.price_m2, level, row.zone_id, row.period, row.import_id, reason)


def get_reference_price(db: Session, zone: Union[int, Zone, object], source: str = SOURCE_REGISTRAL_USADAS, *,
                        update_to=None, index_series: str = DEFAULT_SERIES) -> Optional[ReferencePrice]:
    """None si la zona no existe, no está resuelta o ni ella ni su distrito tienen precio.

    `update_to` (None por defecto: sin actualizar) agrega el precio actualizado con el IPV del INE:
    'latest' = hasta el último trimestre publicado; también acepta un trimestre ('2026T2'), una fecha o un año.
    El precio registral es un promedio anual, así que se actualiza desde el año completo de su período.
    El original queda en `price_m2`; el factor, en `rollforward`. Si no hay índice, `rollforward_error` dice por qué
    y `updated_price_m2` queda en None."""
    ref = _reference_price(db, zone, source)
    if ref is None or update_to is None:
        return ref
    try:
        factor = rollforward_factor(db, ref.period.year, None if update_to == "latest" else update_to, series=index_series)
    except IndexNotAvailable as e:
        return replace(ref, rollforward_error=str(e))
    return replace(ref, updated_price_m2=factor.apply(ref.price_m2), rollforward=factor)


def _reference_price(db: Session, zone, source: str) -> Optional[ReferencePrice]:
    zone_id = _zone_id(zone)
    if zone_id is None:
        return None
    record = db.get(Zone, zone_id)
    if record is None:
        return None
    own = _latest(db, record.id, source)
    if record.level != "barrio":
        return _result(own, record.level) if own else None

    parent = db.get(Zone, record.parent_id) if record.parent_id is not None else None
    district = _latest(db, parent.id, source) if parent is not None else None
    if own is not None and (district is None or own.period >= district.period):
        return _result(own, "barrio")
    if district is not None:
        return _result(district, "distrito", REASON_NO_DATA if own is None else REASON_STALE)
    return _result(own, "barrio") if own is not None else None
