"""Cargador del IPV (índice de precios de vivienda) del INE para la Comunidad de Madrid.

Tabla 79563 del INE: índices trimestrales por comunidad autónoma, base 2025 = 100 (general, vivienda nueva y de
segunda mano). Se lee de dos formas, y `parse_ipv_content` elige por el contenido:

1. El CSV que se baja de la web del INE (data/raw/79563.csv): separado por `;`, codificación cp1252, decimales con coma:
       Total Nacional;Comunidades y Ciudades Autónomas;General, vivienda nueva y de segunda mano;Índices y tasas;Periodo;Total
       Nacional;13 Madrid, Comunidad de;Vivienda segunda mano;Índice;2026T2;112,649
   (las filas sin comunidad son el total nacional y se ignoran; solo se toman las filas de tipo "Índice").

2. La API Tempus, que devuelve, para una tabla, una lista de series:
    [{"COD": "...", "Nombre": "Madrid, Comunidad de. General. Índice.", "Data": [
        {"Fecha": 1735689600000, "FK_Periodo": 19, "Anyo": 2025, "Valor": 98.2, ...}, ...]}, ...]
`parse_tempus_ipv` se queda con las series de índice de la Comunidad de Madrid (general, vivienda nueva y de
segunda mano) y las pasa a puntos trimestrales. Es puro: la red está solo en `fetch_tempus`.

Cada carga queda en market_imports (el mismo contenido dos veces no hace nada) y solo agrega puntos nuevos o
revisados a market_indices; nunca pisa. Hace flush, no commit.
"""
import calendar
import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import requests
from sqlalchemy.orm import Session

from backend.market.zones import normalize_zone_name
from backend.models.market import MarketImport, MarketIndex

SOURCE_INE_IPV = "ine_ipv"
GEOGRAPHY_MADRID = "Comunidad de Madrid"
DEFAULT_TABLE_ID = 79563           # IPV por comunidades, trimestral, base 2025 (la 25171 es base 2015 y terminó en 2025T4)
TEMPUS_URL = "https://servicios.ine.es/wstempus/js/es/DATOS_TABLA/{table_id}"
SERIES = ("general", "nueva", "segunda_mano")
_QUARTER_BY_PERIOD_CODE = {19: 1, 20: 2, 21: 3, 22: 4}      # FK_Periodo de Tempus para T1..T4
_NOT_AN_INDEX = ("variacion", "tasa", "ponderacion", "porcentaje", "contribucion")
IMPORTED = "imported"
ALREADY_IMPORTED = "already_imported"


class IPVParseError(ValueError):
    """La respuesta del INE no tiene el formato esperado."""


class IPVFetchError(RuntimeError):
    """No se pudo consultar la API del INE."""


@dataclass(frozen=True)
class IndexPoint:
    series: str            # general | nueva | segunda_mano
    period: str            # 2026T2
    period_end: date
    value: float


def quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    return date(year, month, calendar.monthrange(year, month)[1])


def period_label(year: int, quarter: int) -> str:
    return f"{year}T{quarter}"


def _housing_kind(words: set[str]) -> Optional[str]:
    if "segunda" in words:
        return "segunda_mano"
    if "nueva" in words:
        return "nueva"
    if "general" in words:
        return "general"
    return None


def _is_madrid_region(normalized: str) -> bool:
    words = set(normalized.split())
    return "madrid" in words and "comunidad" in words


def classify_series(name: str) -> Optional[str]:
    """Nombre de una serie de Tempus → general | nueva | segunda_mano, o None si no es un índice de la
    Comunidad de Madrid (otra región, una variación, una ponderación...)."""
    n = normalize_zone_name(name)
    if not _is_madrid_region(n) or any(w in n for w in _NOT_AN_INDEX):
        return None
    return _housing_kind(set(n.split()))


def _quarter_of(item: dict) -> tuple[int, int]:
    year = item.get("Anyo")
    if not isinstance(year, int):
        raise IPVParseError(f"dato sin año: {item!r}")
    code = item.get("FK_Periodo")
    if code in _QUARTER_BY_PERIOD_CODE:
        return year, _QUARTER_BY_PERIOD_CODE[code]
    millis = item.get("Fecha")                 # respaldo: el mes de la fecha (del inicio o del fin del trimestre)
    if isinstance(millis, (int, float)):
        # +12 h: una medianoche en hora de España cae a las 22:00/23:00 UTC del día anterior y movería la fecha de mes.
        month = datetime.fromtimestamp(millis / 1000 + 12 * 3600, tz=timezone.utc).month
        return year, (month - 1) // 3 + 1
    raise IPVParseError(f"no se pudo determinar el trimestre de {item!r} (¿serie no trimestral?)")


def parse_tempus_ipv(payload) -> list[IndexPoint]:
    """Puntos trimestrales de las series de índice de la Comunidad de Madrid. Falla si no encuentra ninguna,
    si una serie aparece dos veces o si un valor no es un índice razonable."""
    if not isinstance(payload, list):
        raise IPVParseError("se esperaba una lista de series")
    found: dict[str, list[IndexPoint]] = {}
    for serie in payload:
        if not isinstance(serie, dict):
            continue
        kind = classify_series(str(serie.get("Nombre", "")))
        if kind is None:
            continue
        if kind in found:
            raise IPVParseError(f"la serie {kind!r} de Madrid aparece más de una vez ({serie.get('Nombre')!r})")
        points = []
        for item in serie.get("Data") or []:
            value = item.get("Valor")
            if value is None:
                continue                       # trimestre aún sin publicar
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1000:
                raise IPVParseError(f"valor no válido en {serie.get('Nombre')!r}: {value!r}")
            year, quarter = _quarter_of(item)
            points.append(IndexPoint(kind, period_label(year, quarter), quarter_end(year, quarter), float(value)))
        if len({p.period for p in points}) != len(points):
            raise IPVParseError(f"la serie {kind!r} repite trimestres")
        found[kind] = sorted(points, key=lambda p: p.period_end)
    if not found:
        raise IPVParseError("no hay ninguna serie de índice de la Comunidad de Madrid en la respuesta")
    return [p for kind in SERIES for p in found.get(kind, [])]


def matched_series_names(payload) -> dict[str, str]:
    """{serie: nombre de Tempus} de las series elegidas, para mostrarlas en el dry-run."""
    return {classify_series(str(s.get("Nombre", ""))): str(s.get("Nombre"))
            for s in payload if isinstance(s, dict) and classify_series(str(s.get("Nombre", "")))}


_CSV_COLUMNS = 6
_CSV_PERIOD_RE = re.compile(r"(\d{4})T([1-4])")


def decode_ine_csv(content: bytes) -> str:
    """El CSV de la web del INE viene en cp1252; si ya fuera UTF-8 (con o sin BOM) también se acepta."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


def _spanish_number(raw: str) -> float:
    text = raw.strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return float(text)


@dataclass
class ParsedIpv:
    points: list[IndexPoint]
    series_names: dict[str, str]          # serie → cómo se llama en el archivo (para mostrar y verificar)
    format: str                           # 'csv' | 'json'


def parse_ine_csv(content: bytes) -> ParsedIpv:
    """CSV de la tabla del INE → puntos de la Comunidad de Madrid. Estricto con la cabecera y con cada fila."""
    reader = csv.reader(io.StringIO(decode_ine_csv(content), newline=""), delimiter=";")
    found: dict[str, dict[str, IndexPoint]] = {}
    names: dict[str, str] = {}
    for fields in reader:
        line = reader.line_num
        if not fields or all(not f.strip() for f in fields):
            continue
        if line == 1:
            if len(fields) != _CSV_COLUMNS or normalize_zone_name(fields[4]) != "periodo" or normalize_zone_name(fields[5]) != "total":
                raise IPVParseError(f"línea 1: cabecera inesperada {fields!r}; se esperaban {_CSV_COLUMNS} columnas terminadas en Periodo;Total")
            continue
        if len(fields) != _CSV_COLUMNS:
            raise IPVParseError(f"línea {line}: {len(fields)} columnas, se esperaban {_CSV_COLUMNS}")
        _, region, housing, kind_col, period, raw_value = (f.strip() for f in fields)
        if not _is_madrid_region(normalize_zone_name(region)):
            continue                                         # total nacional u otra comunidad
        if normalize_zone_name(kind_col) != "indice":
            continue                                         # variaciones y otras tasas
        series = _housing_kind(set(normalize_zone_name(housing).split()))
        if series is None:
            continue
        match = _CSV_PERIOD_RE.fullmatch(period)
        if not match:
            raise IPVParseError(f"línea {line}: periodo no válido {period!r} (se esperaba, por ejemplo, 2026T2)")
        if raw_value in ("", ".", "..", "-"):
            continue                                         # trimestre sin publicar
        try:
            value = _spanish_number(raw_value)
        except ValueError:
            raise IPVParseError(f"línea {line}: valor no válido {raw_value!r}") from None
        if not 0 < value < 1000:
            raise IPVParseError(f"línea {line}: valor fuera de rango {raw_value!r}")
        year, quarter = int(match.group(1)), int(match.group(2))
        label = period_label(year, quarter)
        if label in found.setdefault(series, {}):
            raise IPVParseError(f"línea {line}: la serie {series!r} repite {label}")
        found[series][label] = IndexPoint(series, label, quarter_end(year, quarter), value)
        names.setdefault(series, f"{region} / {housing} / {kind_col}")
    if not found:
        raise IPVParseError("no hay filas de índice de la Comunidad de Madrid en el CSV")
    points = [p for kind in SERIES for p in sorted(found.get(kind, {}).values(), key=lambda p: p.period_end)]
    return ParsedIpv(points, names, "csv")


def parse_ipv_content(content: bytes) -> ParsedIpv:
    """JSON de Tempus o CSV de la web del INE, según el contenido."""
    if content.lstrip()[:1] in (b"[", b"{"):
        try:
            payload = json.loads(content)
        except ValueError as e:
            raise IPVParseError(f"el contenido no es JSON: {e}") from None
        return ParsedIpv(parse_tempus_ipv(payload), matched_series_names(payload) if isinstance(payload, list) else {}, "json")
    return parse_ine_csv(content)


def fetch_tempus(table_id: int = DEFAULT_TABLE_ID, nult: Optional[int] = 20, timeout: float = 30, http=requests) -> bytes:
    """Descarga la tabla de la API Tempus (últimos `nult` datos por serie). `http` se puede reemplazar en tests."""
    params = {"nult": nult} if nult else {}
    try:
        response = http.get(TEMPUS_URL.format(table_id=table_id), params=params, timeout=timeout,
                            headers={"Accept": "application/json"})
        response.raise_for_status()
        content = response.content
        json.loads(content)
    except requests.RequestException as e:
        raise IPVFetchError(f"la API del INE no respondió bien: {e}") from None
    except ValueError:
        raise IPVFetchError("la API del INE no devolvió JSON") from None
    return content


@dataclass
class IpvImportResult:
    status: str
    sha256: str
    import_id: Optional[int] = None
    points_read: int = 0
    inserted: int = 0
    unchanged: int = 0
    revised: int = 0               # de los insertados, los que corrigen un valor ya cargado
    period_from: Optional[str] = None
    period_to: Optional[str] = None

    @property
    def changed(self) -> bool:
        return self.status == IMPORTED


def _latest_values(db: Session, source: str, geography: str) -> dict[tuple[str, str], float]:
    latest: dict[tuple[str, str], float] = {}
    rows = (db.query(MarketIndex).filter_by(source=source, geography=geography)
            .order_by(MarketIndex.import_id.desc()))
    for row in rows:
        latest.setdefault((row.series, row.period), row.value)
    return latest


def import_ipv(db: Session, content: bytes, file_name: str, geography: str = GEOGRAPHY_MADRID,
               now: Optional[datetime] = None) -> IpvImportResult:
    sha = hashlib.sha256(content).hexdigest()
    previous = db.query(MarketImport).filter_by(source=SOURCE_INE_IPV, sha256=sha).first()
    if previous is not None:
        return IpvImportResult(ALREADY_IMPORTED, sha, import_id=previous.id)

    parsed = parse_ipv_content(content)
    points = parsed.points

    latest = _latest_values(db, SOURCE_INE_IPV, geography)
    new = [p for p in points if latest.get((p.series, p.period)) is None or abs(latest[(p.series, p.period)] - p.value) > 1e-9]
    revised = sum(1 for p in new if (p.series, p.period) in latest)
    first, last = points_range(points)
    record = MarketImport(
        source=SOURCE_INE_IPV, file_name=file_name, sha256=sha, period_label=f"{first}-{last}",
        loaded_at=now or datetime.utcnow(), row_count=len(new), skipped_count=len(points) - len(new),
        notes=f"{geography}; formato {parsed.format}; series: {', '.join(sorted({p.series for p in points}))}")
    db.add(record)
    db.flush()
    db.add_all(MarketIndex(source=SOURCE_INE_IPV, geography=geography, series=p.series, period=p.period,
                           period_end=p.period_end, value=p.value, import_id=record.id) for p in new)
    db.flush()
    return IpvImportResult(IMPORTED, sha, import_id=record.id, points_read=len(points), inserted=len(new),
                           unchanged=len(points) - len(new), revised=revised, period_from=first, period_to=last)


def points_range(points: list[IndexPoint]) -> tuple[str, str]:
    ordered = sorted(points, key=lambda p: p.period_end)
    return ordered[0].period, ordered[-1].period
