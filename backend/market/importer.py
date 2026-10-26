"""Importa el CSV registral a market_imports + market_prices.

Reglas (docs/market_prices_design.md):
  - la carga queda registrada en market_imports (fuente, archivo, sha256, período, filas);
  - el mismo archivo (misma fuente y sha256) dos veces no hace nada;
  - un archivo distinto agrega filas y nunca pisa las anteriores;
  - las filas sin dato (`..` y `0,00`) no se insertan: el fallback del barrio al distrito se resuelve al consultar
    (backend/market/reference.py);
  - las zonas tienen que estar sembradas (scripts/seed_zones.py).

`import_registral` hace flush, no commit: quien la llama decide si confirma o descarta (dry-run).
"""
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from backend.market.registral import RegistralRow, parse_registral_text
from backend.market.zones import ZoneKey
from backend.models.market import MarketImport, MarketPrice
from backend.models.zone import Zone

SOURCE_REGISTRAL_USADAS = "registral_usadas"
IMPORTED = "imported"
ALREADY_IMPORTED = "already_imported"


class MarketImportError(ValueError):
    """El archivo no se puede importar (zonas sin sembrar, tipo de vivienda equivocado...). No se escribe nada."""


@dataclass
class ImportResult:
    status: str                                   # IMPORTED | ALREADY_IMPORTED
    sha256: str
    import_id: Optional[int] = None
    rows_read: int = 0
    inserted: int = 0
    skipped_no_data: int = 0
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    zones_with_data: int = 0

    @property
    def changed(self) -> bool:
        return self.status == IMPORTED


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def period_end(year: int) -> date:
    return date(year, 12, 31)


def row_zone_key(row: RegistralRow) -> ZoneKey:
    if row.level == "ciudad":
        return ("ciudad", None)
    if row.level == "distrito":
        return ("distrito", row.district_code)
    return ("barrio", row.barrio_code)


def import_registral(db: Session, content: bytes, file_name: str, source: str = SOURCE_REGISTRAL_USADAS,
                     now: Optional[datetime] = None) -> ImportResult:
    sha = file_sha256(content)
    previous = db.query(MarketImport).filter_by(source=source, sha256=sha).first()
    if previous is not None:
        return ImportResult(ALREADY_IMPORTED, sha, import_id=previous.id, inserted=0)

    rows = parse_registral_text(content.decode("utf-8"))
    if source == SOURCE_REGISTRAL_USADAS:
        other = sorted({r.housing_type for r in rows} - {"usadas"})
        if other:
            raise MarketImportError(f"la fuente {source!r} es de vivienda usada y el archivo trae {other}")

    zone_ids = {(z.level, z.code): z.id for z in db.query(Zone).all()}
    missing = sorted({row_zone_key(r) for r in rows} - set(zone_ids), key=str)
    if missing:
        shown = ", ".join(f"{level}:{code}" for level, code in missing[:10])
        raise MarketImportError(
            f"{len(missing)} zonas del archivo no están en la base ({shown}{'…' if len(missing) > 10 else ''}). "
            "Sembralas primero: python -m scripts.seed_zones --confirm")

    with_price = [r for r in rows if r.price_m2 is not None]
    years = sorted({r.year for r in rows})
    record = MarketImport(
        source=source, file_name=file_name, sha256=sha, period_label=f"{years[0]}-{years[-1]}",
        loaded_at=now or datetime.utcnow(), row_count=len(with_price), skipped_count=len(rows) - len(with_price))
    db.add(record)
    db.flush()
    db.add_all(MarketPrice(zone_id=zone_ids[row_zone_key(r)], source=source, period=period_end(r.year),
                           price_m2=r.price_m2, import_id=record.id) for r in with_price)
    db.flush()
    return ImportResult(
        IMPORTED, sha, import_id=record.id, rows_read=len(rows), inserted=len(with_price),
        skipped_no_data=len(rows) - len(with_price), period_from=period_end(years[0]), period_to=period_end(years[-1]),
        zones_with_data=len({row_zone_key(r) for r in with_price}))


def import_registral_file(db: Session, path: str | Path, source: str = SOURCE_REGISTRAL_USADAS) -> ImportResult:
    path = Path(path)
    return import_registral(db, path.read_bytes(), path.name, source)
