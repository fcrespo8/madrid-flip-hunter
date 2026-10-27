"""Factor para actualizar un precio de un período a otro con el IPV del INE (market_indices).

    precio_hoy = precio_registral × rollforward_factor(db, desde=2025).factor

`desde` / `hasta` aceptan:
  - un año (int, 2025): el índice anual, promedio de sus cuatro trimestres (el registral es un promedio anual);
  - un trimestre ('2026T2', '2026-T2', '2026Q2');
  - una fecha (date/datetime): el trimestre que la contiene.
`hasta=None` es "hoy": el último trimestre publicado de la serie. Nunca se extrapola más allá del último dato.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Union

from sqlalchemy.orm import Session

from backend.market.ipv import GEOGRAPHY_MADRID, SERIES, SOURCE_INE_IPV, period_label
from backend.models.market import MarketIndex

DEFAULT_SERIES = "segunda_mano"      # el registral es de vivienda usada
PeriodLike = Union[int, str, date, datetime]
_QUARTER_RE = re.compile(r"(\d{4})\s*[-_ ]?\s*[TQtq]\s*([1-4])")


class IndexNotAvailable(ValueError):
    """Falta el índice para el período pedido (no cargado, o el año no está completo)."""


@dataclass(frozen=True)
class Period:
    year: int
    quarter: Optional[int] = None          # None = año completo

    @property
    def label(self) -> str:
        return str(self.year) if self.quarter is None else period_label(self.year, self.quarter)


def parse_period(value: PeriodLike) -> Period:
    if isinstance(value, bool):
        raise ValueError(f"período no válido: {value!r}")
    if isinstance(value, int):
        if not 1900 < value < 2200:
            raise ValueError(f"año no válido: {value!r}")
        return Period(value)
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return Period(value.year, (value.month - 1) // 3 + 1)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{4}", text):
            return parse_period(int(text))
        match = _QUARTER_RE.fullmatch(text)
        if match:
            return Period(int(match.group(1)), int(match.group(2)))
    raise ValueError(f"período no válido: {value!r} (use 2025, '2026T2' o una fecha)")


def _quarterly_values(db: Session, series: str, geography: str, source: str) -> dict[str, float]:
    """{'2026T2': valor}, con el valor de la carga más reciente de cada trimestre."""
    values: dict[str, float] = {}
    rows = (db.query(MarketIndex).filter_by(source=source, geography=geography, series=series)
            .order_by(MarketIndex.import_id.desc()))
    for row in rows:
        values.setdefault(row.period, row.value)
    return values


def latest_quarter(values: dict[str, float]) -> Optional[Period]:
    if not values:
        return None
    return max((parse_period(label) for label in values), key=lambda p: (p.year, p.quarter))


def index_value(values: dict[str, float], period: Period, series: str = DEFAULT_SERIES) -> float:
    if period.quarter is not None:
        if period.label not in values:
            raise IndexNotAvailable(f"no hay índice {series} para {period.label}")
        return values[period.label]
    quarters = [period_label(period.year, q) for q in (1, 2, 3, 4)]
    missing = [q for q in quarters if q not in values]
    if missing:
        raise IndexNotAvailable(f"el año {period.year} no está completo en el índice {series} (faltan {', '.join(missing)})")
    return sum(values[q] for q in quarters) / 4


@dataclass(frozen=True)
class RollforwardFactor:
    factor: float
    desde: str                  # '2025' o '2025T4'
    hasta: str                  # '2026T2'
    index_from: float
    index_to: float
    series: str
    geography: str

    def __float__(self) -> float:
        return self.factor

    def apply(self, price: float) -> float:
        return price * self.factor


def rollforward_factor(db: Session, desde: PeriodLike, hasta: Optional[PeriodLike] = None, *,
                       series: str = DEFAULT_SERIES, geography: str = GEOGRAPHY_MADRID,
                       source: str = SOURCE_INE_IPV) -> RollforwardFactor:
    """índice(hasta) / índice(desde). `hasta=None`: el último trimestre publicado."""
    if series not in SERIES:
        raise ValueError(f"serie no válida: {series!r} (use {', '.join(SERIES)})")
    values = _quarterly_values(db, series, geography, source)
    if not values:
        raise IndexNotAvailable(f"no hay índices {series} de {geography} cargados (python -m scripts.load_ipv)")
    start = parse_period(desde)
    end = parse_period(hasta) if hasta is not None else latest_quarter(values)
    index_from, index_to = index_value(values, start, series), index_value(values, end, series)
    return RollforwardFactor(index_to / index_from, start.label, end.label, index_from, index_to, series, geography)
