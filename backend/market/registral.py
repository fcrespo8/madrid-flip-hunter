"""Parser del CSV de precio registral por barrio (data/raw/precio_registral_barrio.csv).

Formato (ver data/raw/README.md): UTF-8 con BOM, separador `;`, números a la española
(`5.333,59`). Sin dato: `..` (barrio no publicado) o `0,00` (barrio con menos de 15 compraventas).
Ambos salen como `price_m2 = None`: un 0 nunca es un precio.

Las funciones de este módulo son puras: no tocan la base ni la red. Solo `load_registral_csv` lee un archivo.
"""
import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional

HEADER = ["Distrito", "Barrio", "Año", "Precio medio y números índice", "Tipo de vivienda", "Total"]
CITY_NAME = "Ciudad de Madrid"
EXPECTED_UNIT = "Euros/m2"
HOUSING_TYPES = {"Usadas": "usadas", "Nuevas": "nuevas", "Total": "total"}
NO_DATA_MARKER = ".."

Level = Literal["ciudad", "distrito", "barrio"]

_NUMBER_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?")
_ZONE_RE = re.compile(r"(?:(\d+)\.\s+)?(.+)")        # "011. Palacio" → ("011", "Palacio")


class RegistralParseError(ValueError):
    """El archivo no tiene el formato esperado. El mensaje trae el número de línea."""


@dataclass(frozen=True)
class RegistralRow:
    year: int
    level: Level
    district_code: Optional[str]      # "01"; None en la fila de la ciudad
    district_name: Optional[str]      # "Centro"; None en la fila de la ciudad
    barrio_code: Optional[str]        # "011"; solo en nivel barrio
    barrio_name: Optional[str]        # "Palacio"; solo en nivel barrio
    housing_type: str                 # "usadas" (hoy el archivo solo trae ese tipo)
    price_m2: Optional[float]         # None = sin dato


def parse_price(raw: str) -> Optional[float]:
    """'5.333,59' → 5333.59. '..' y cualquier cero ('0,00', '0') → None (sin dato).
    Levanta ValueError ante algo que no sea un número en formato español."""
    text = raw.strip()
    if text == NO_DATA_MARKER:
        return None
    if not _NUMBER_RE.fullmatch(text):
        raise ValueError(f"número no válido: {raw!r}")
    value = float(text.replace(".", "").replace(",", "."))
    return value if value > 0 else None


def _split_zone(raw: str, line: int) -> tuple[Optional[str], str]:
    match = _ZONE_RE.fullmatch(raw.strip())
    if not match:
        raise RegistralParseError(f"línea {line}: zona vacía")
    return match.group(1), match.group(2).strip()


def parse_registral_text(text: str) -> list[RegistralRow]:
    """Parsea el contenido del CSV. Estricto: cabecera, unidad, tipo de vivienda, año, número de
    columnas, formato numérico y filas duplicadas que no sean las esperadas levantan RegistralParseError."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), delimiter=";")
    rows: list[RegistralRow] = []
    seen: set[tuple] = set()

    for fields in reader:
        line = reader.line_num
        if not fields:
            continue
        if line == 1:
            if fields != HEADER:
                raise RegistralParseError(f"línea 1: cabecera inesperada {fields!r}")
            continue
        if len(fields) != len(HEADER):
            raise RegistralParseError(f"línea {line}: {len(fields)} columnas, se esperaban {len(HEADER)}")
        district_raw, barrio_raw, year_raw, unit, housing_raw, total_raw = fields

        if unit != EXPECTED_UNIT:
            raise RegistralParseError(f"línea {line}: unidad {unit!r}, se esperaba {EXPECTED_UNIT!r}")
        if housing_raw not in HOUSING_TYPES:
            raise RegistralParseError(f"línea {line}: tipo de vivienda desconocido {housing_raw!r}")
        if not re.fullmatch(r"\d{4}", year_raw):
            raise RegistralParseError(f"línea {line}: año no válido {year_raw!r}")
        try:
            price = parse_price(total_raw)
        except ValueError as e:
            raise RegistralParseError(f"línea {line}: {e}") from None

        if district_raw == CITY_NAME:
            if barrio_raw != CITY_NAME:
                raise RegistralParseError(f"línea {line}: fila de la ciudad con barrio {barrio_raw!r}")
            level, d_code, d_name, b_code, b_name = "ciudad", None, None, None, None
        else:
            d_code, d_name = _split_zone(district_raw, line)
            if barrio_raw == district_raw:
                level, b_code, b_name = "distrito", None, None
            else:
                level = "barrio"
                b_code, b_name = _split_zone(barrio_raw, line)
                if d_code is None or b_code is None or not b_code.startswith(d_code):
                    raise RegistralParseError(
                        f"línea {line}: el barrio {barrio_raw!r} no cuelga del distrito {district_raw!r}")

        key = (int(year_raw), level, d_code, b_code, HOUSING_TYPES[housing_raw])
        if key in seen:
            raise RegistralParseError(f"línea {line}: fila repetida {key!r}")
        seen.add(key)
        rows.append(RegistralRow(int(year_raw), level, d_code, d_name, b_code, b_name,
                                 HOUSING_TYPES[housing_raw], price))

    if not rows:
        raise RegistralParseError("el archivo no tiene filas de datos")
    return rows


def load_registral_csv(path: str | Path) -> list[RegistralRow]:
    """Lee el archivo (UTF-8, con o sin BOM) y lo parsea."""
    return parse_registral_text(Path(path).read_text(encoding="utf-8"))


def latest_year(rows: Iterable[RegistralRow]) -> int:
    return max(r.year for r in rows)
