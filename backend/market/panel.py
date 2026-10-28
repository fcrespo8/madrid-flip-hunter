"""Importador del Panel de indicadores de distritos y barrios de datos.madrid.es (CSV acumulado, formato largo).

Solo importa una lista FIJA de indicadores (nueve; sin nacionalidad: ver docs/market_prices_design.md y el RAG), buscados por
su nombre normalizado. Si alguno desaparece o cambia de nombre, el importador se niega a seguir (PanelIndicatorMissing) y
sugiere los nombres parecidos, salvo `allow_missing=True`.

Reglas:
  - cada carga queda en market_imports (fuente `madrid_panel`); el mismo archivo (mismo sha256) dos veces no hace nada;
  - solo se insertan filas: los valores nuevos o que cambiaron respecto de lo ya cargado; nunca se pisa;
  - si dos ediciones del panel traen el mismo (indicador, zona, año), vale la más nueva;
  - la unidad es la canónica del indicador (el archivo la escribe de varias formas: 'Media   ', 'Proporción'/'Porcentaje');
  - los códigos del archivo vienen sin el cero inicial: barrio `11` → `011`, distrito `1` → `01`.
`import_panel` hace flush, no commit.
"""
import csv
import difflib
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from sqlalchemy.orm import Session

from backend.market.zones import ZoneKey, normalize_zone_name
from backend.models.market import MarketImport, ZoneIndicator
from backend.models.zone import Zone

SOURCE_MADRID_PANEL = "madrid_panel"
PANEL_PACKAGE_URL = "https://datos.madrid.es/api/3/action/package_show?id=300087-0-indicadores-distritos"
IMPORTED = "imported"
ALREADY_IMPORTED = "already_imported"
REQUIRED_COLUMNS = ("Periodo panel", "cod_distrito", "cod_barrio", "año", "indicador_completo", "valor_indicador")


@dataclass(frozen=True)
class IndicatorSpec:
    key: str                  # clave estable en zone_indicators.indicator
    panel_name: str           # nombre completo en el Panel (indicador_completo); se compara normalizado
    unit: str                 # unidad canónica
    meaning: str              # para qué sirve

    @property
    def name_norm(self) -> str:
        return normalize_zone_name(self.panel_name)


INDICATORS = (
    IndicatorSpec("renta_neta_hogar", "Renta neta media anual de los hogares (Urban Audit)", "eur/hogar/año",
                  "capacidad de pago de los compradores"),
    IndicatorSpec("valor_catastral_medio", "Valor catastral medio por inmueble de uso residencial", "eur/inmueble",
                  "nivel de valor de la zona, independiente del mercado"),
    IndicatorSpec("anio_medio_construccion", "Año medio de construcción de inmuebles de uso residencial", "año",
                  "antigüedad del parque: alcance probable de la reforma"),
    IndicatorSpec("tasa_paro", "Tasa absoluta de paro registrado (febrero)", "%",
                  "salud económica del barrio"),
    IndicatorSpec("crecimiento_poblacion", "Tasa de crecimiento demográfico (porcentaje)", "%",
                  "tendencia de la demanda residencial"),
    IndicatorSpec("tamano_medio_hogar", "Tamaño medio del hogar", "personas/hogar",
                  "perfil del comprador y distribución que se vende"),
    IndicatorSpec("envejecimiento", "Porcentaje de envejecimiento (población mayor de 65 años/población total)", "%",
                  "perfil demográfico del barrio"),
    IndicatorSpec("locales_cerrados", "Número de locales dados de alta cerrados", "locales",
                  "vitalidad comercial: persianas bajadas"),
    IndicatorSpec("densidad_poblacion", "Población densidad (hab./ha.)", "hab/ha",
                  "profundidad de la demanda y liquidez"),
)
BY_NAME = {spec.name_norm: spec for spec in INDICATORS}


class PanelParseError(ValueError):
    """El CSV del panel no tiene el formato esperado. El mensaje trae el número de línea."""


class PanelFetchError(RuntimeError):
    """No se pudo descargar el panel de datos.madrid.es."""


class PanelImportError(ValueError):
    """No se puede importar (zonas sin sembrar...). No se escribe nada."""


class PanelIndicatorMissing(PanelImportError):
    def __init__(self, missing: list[IndicatorSpec], suggestions: dict[str, list[str]]):
        self.missing, self.suggestions = missing, suggestions
        detail = "; ".join(f"{spec.key} ({spec.panel_name!r}"
                           + (f", ¿será {suggestions[spec.key][0]!r}?" if suggestions.get(spec.key) else "") + ")" for spec in missing)
        super().__init__(f"faltan {len(missing)} indicadores en el panel: {detail}")


@dataclass(frozen=True)
class PanelValue:
    indicator: str
    zone: ZoneKey               # ('barrio', '011') | ('distrito', '01') | ('ciudad', None)
    year: int
    value: float
    edition: int                # 'Periodo panel' de donde salió


@dataclass
class PanelParse:
    values: list[PanelValue]
    barrio_rows: dict[str, int]                                  # indicador → filas a nivel barrio
    superseded: int = 0                                          # valores reemplazados por una edición más nueva
    skipped_no_value: int = 0
    skipped_no_year: int = 0
    other_names: set[str] = field(default_factory=set)           # nombres de indicadores que no están en la lista (a nivel barrio)

    @property
    def missing(self) -> list[IndicatorSpec]:
        return [spec for spec in INDICATORS if not self.barrio_rows.get(spec.key)]

    def suggestions(self) -> dict[str, list[str]]:
        return {spec.key: difflib.get_close_matches(spec.name_norm, sorted(self.other_names), n=3, cutoff=0.6)
                for spec in self.missing}


def decode_panel(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


def parse_number(raw: str) -> float:
    """'1.303' → 1303; '43,89' → 43.89; '136.594,48' → 136594.48; '523' → 523. Lo demás es un error."""
    text = raw.strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif "." in text:
        if not (text.lstrip("-").replace(".", "").isdigit() and all(len(g) == 3 for g in text.lstrip("-").split(".")[1:])):
            raise ValueError(f"número ambiguo: {raw!r}")
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"número no válido: {raw!r}") from None


def _zone_key(distrito: str, barrio: str, line: int) -> ZoneKey:
    if barrio.strip():
        if not barrio.strip().isdigit():
            raise PanelParseError(f"línea {line}: cod_barrio no válido {barrio!r}")
        return ("barrio", barrio.strip().zfill(3))
    if distrito.strip():
        if not distrito.strip().isdigit():
            raise PanelParseError(f"línea {line}: cod_distrito no válido {distrito!r}")
        return ("distrito", distrito.strip().zfill(2))
    return ("ciudad", None)


def parse_panel_csv(content: bytes) -> PanelParse:
    reader = csv.reader(io.StringIO(decode_panel(content), newline=""), delimiter=";")
    columns: Optional[dict[str, int]] = None
    best: dict[tuple, PanelValue] = {}
    out = PanelParse([], {})
    for fields in reader:
        line = reader.line_num
        if not fields or all(not f.strip() for f in fields):
            continue
        if columns is None:
            columns = {name.strip(): i for i, name in enumerate(fields)}
            absent = [c for c in REQUIRED_COLUMNS if c not in columns]
            if absent:
                raise PanelParseError(f"línea 1: faltan columnas {absent}; cabecera {fields!r}")
            width = len(fields)
            continue
        if len(fields) != width:
            raise PanelParseError(f"línea {line}: {len(fields)} columnas, se esperaban {width}")
        name_norm = normalize_zone_name(fields[columns["indicador_completo"]])
        zone = _zone_key(fields[columns["cod_distrito"]], fields[columns["cod_barrio"]], line)
        spec = BY_NAME.get(name_norm)
        if spec is None:
            if zone[0] == "barrio":
                out.other_names.add(name_norm)
            continue
        if zone[0] == "barrio":
            out.barrio_rows[spec.key] = out.barrio_rows.get(spec.key, 0) + 1
        year_raw, value_raw = fields[columns["año"]].strip(), fields[columns["valor_indicador"]].strip()
        if not year_raw:
            out.skipped_no_year += 1
            continue
        if not value_raw:
            out.skipped_no_value += 1
            continue
        try:
            year, value = int(year_raw), parse_number(value_raw)
            edition = int(fields[columns["Periodo panel"]].strip())
        except ValueError as e:
            raise PanelParseError(f"línea {line}: {e} (año {year_raw!r}, valor {value_raw!r})") from None
        key = (spec.key, zone, year)
        current = best.get(key)
        item = PanelValue(spec.key, zone, year, value, edition)
        if current is None:
            best[key] = item
        elif edition == current.edition:
            if abs(current.value - value) > 1e-9:
                raise PanelParseError(f"línea {line}: {spec.key} {zone} {year} repetido en la edición {edition} con otro valor")
        else:
            out.superseded += 1
            if edition > current.edition:
                best[key] = item
    if columns is None:
        raise PanelParseError("el archivo está vacío")
    out.values = sorted(best.values(), key=lambda v: (v.indicator, v.zone[0], v.zone[1] or "", v.year))
    return out


def fetch_panel(timeout: float = 120, http=requests) -> tuple[bytes, str]:
    """Descarga el CSV acumulado del panel resolviendo su URL con la API CKAN del portal."""
    try:
        meta = http.get(PANEL_PACKAGE_URL, timeout=30, headers={"Accept": "application/json"})
        meta.raise_for_status()
        resources = meta.json()["result"]["resources"]
        csvs = [r for r in resources if (r.get("format") or "").upper() == "CSV"]
        chosen = next((r for r in csvs if str(r.get("url", "")).endswith("acumulado.csv")), csvs[0] if csvs else None)
        if chosen is None:
            raise PanelFetchError("el dataset del panel no tiene ningún recurso CSV")
        response = http.get(chosen["url"], timeout=timeout)
        response.raise_for_status()
        if not response.content:
            raise PanelFetchError("el CSV del panel vino vacío")
        return response.content, chosen["url"]
    except requests.RequestException as e:
        raise PanelFetchError(f"datos.madrid.es no respondió bien: {e}") from None
    except (KeyError, ValueError) as e:
        raise PanelFetchError(f"respuesta inesperada de la API del portal: {e}") from None


@dataclass
class PanelImportResult:
    status: str
    sha256: str
    import_id: Optional[int] = None
    values_read: int = 0
    inserted: int = 0
    unchanged: int = 0
    revised: int = 0
    superseded: int = 0
    period_from: Optional[int] = None
    period_to: Optional[int] = None
    per_indicator: dict[str, dict] = field(default_factory=dict)       # clave → {rows, inserted, years, barrios}
    missing: list[IndicatorSpec] = field(default_factory=list)
    suggestions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.status == IMPORTED


def import_panel(db: Session, content: bytes, file_name: str, allow_missing: bool = False,
                 now: Optional[datetime] = None) -> PanelImportResult:
    sha = hashlib.sha256(content).hexdigest()
    previous = db.query(MarketImport).filter_by(source=SOURCE_MADRID_PANEL, sha256=sha).first()
    if previous is not None:
        return PanelImportResult(ALREADY_IMPORTED, sha, import_id=previous.id)

    parsed = parse_panel_csv(content)
    missing, suggestions = parsed.missing, parsed.suggestions()
    if missing and not allow_missing:
        raise PanelIndicatorMissing(missing, suggestions)

    zone_ids = {(z.level, z.code): z.id for z in db.query(Zone).all()}
    absent = sorted({v.zone for v in parsed.values} - set(zone_ids), key=str)
    if absent:
        shown = ", ".join(f"{level}:{code}" for level, code in absent[:10])
        raise PanelImportError(f"{len(absent)} zonas del panel no están en la base ({shown}{'…' if len(absent) > 10 else ''}). "
                               "Sembralas primero: python -m scripts.seed_zones --confirm")
    if not parsed.values:
        raise PanelImportError("el panel no trae ningún valor de los indicadores fijos")

    latest: dict[tuple, float] = {}
    for row in db.query(ZoneIndicator).order_by(ZoneIndicator.import_id.desc()):
        latest.setdefault((row.zone_id, row.indicator, row.year), row.value)
    new = []
    revised = 0
    for v in parsed.values:
        stored = latest.get((zone_ids[v.zone], v.indicator, v.year))
        if stored is None or abs(stored - v.value) > 1e-9:
            new.append(v)
            revised += stored is not None

    years = sorted({v.year for v in parsed.values})
    editions = sorted({v.edition for v in parsed.values})
    unit_by_key = {spec.key: spec.unit for spec in INDICATORS}
    record = MarketImport(
        source=SOURCE_MADRID_PANEL, file_name=file_name, sha256=sha, period_label=f"{years[0]}-{years[-1]}",
        loaded_at=now or datetime.utcnow(), row_count=len(new), skipped_count=len(parsed.values) - len(new),
        notes=json.dumps({"indicadores": sorted({v.indicator for v in parsed.values}), "ediciones": [editions[0], editions[-1]],
                          "reemplazados_por_edicion_mas_nueva": parsed.superseded, "faltantes": [s.key for s in missing]}, ensure_ascii=False))
    db.add(record)
    db.flush()
    db.add_all(ZoneIndicator(zone_id=zone_ids[v.zone], indicator=v.indicator, year=v.year, value=v.value,
                             unit=unit_by_key[v.indicator], import_id=record.id) for v in new)
    db.flush()

    per: dict[str, dict] = {}
    for v in parsed.values:
        info = per.setdefault(v.indicator, {"rows": 0, "inserted": 0, "years": set(), "barrios": set()})
        info["rows"] += 1
        info["years"].add(v.year)
        if v.zone[0] == "barrio":
            info["barrios"].add(v.zone[1])
    for v in new:
        per[v.indicator]["inserted"] += 1
    return PanelImportResult(
        IMPORTED, sha, import_id=record.id, values_read=len(parsed.values), inserted=len(new),
        unchanged=len(parsed.values) - len(new), revised=revised, superseded=parsed.superseded,
        period_from=years[0], period_to=years[-1],
        per_indicator={k: {"rows": i["rows"], "inserted": i["inserted"], "years": (min(i["years"]), max(i["years"])),
                           "barrios": len(i["barrios"])} for k, i in per.items()},
        missing=missing, suggestions=suggestions)


@dataclass(frozen=True)
class IndicatorValue:
    indicator: str
    value: float
    year: int
    unit: str
    zone_id: int
    import_id: int


def get_zone_indicators(db: Session, zone, indicators: Optional[list[str]] = None) -> dict[str, IndicatorValue]:
    """El último valor (año más nuevo; a igual año, la carga más reciente) de cada indicador de una zona.
    `zone`: id, `Zone` o el `ZoneMatch` del resolvedor. Sin zona o sin datos: {}. No hereda del distrito."""
    zone_id = zone if isinstance(zone, int) and not isinstance(zone, bool) else (getattr(zone, "zone_id", None) or getattr(zone, "id", None))
    if zone_id is None:
        return {}
    query = db.query(ZoneIndicator).filter_by(zone_id=zone_id)
    if indicators is not None:
        query = query.filter(ZoneIndicator.indicator.in_(indicators))
    result: dict[str, IndicatorValue] = {}
    for row in query.order_by(ZoneIndicator.year.desc(), ZoneIndicator.import_id.desc()):
        result.setdefault(row.indicator, IndicatorValue(row.indicator, row.value, row.year, row.unit, row.zone_id, row.import_id))
    return result
