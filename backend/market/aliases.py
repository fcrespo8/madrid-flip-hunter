"""Alias manuales de zonas (data/aliases.csv): textos con que los anuncios nombran una zona.

Formato (CSV con comas, UTF-8, cabecera `texto,zona,nota`):
  texto  cómo aparece en el anuncio (se normaliza igual que los nombres de zona)
  zona   destino: `distrito:20`, `barrio:026`, `ciudad` o `out_of_scope` (otro municipio)
  nota   por qué (obligatoria: un alias sin explicación es un misterio dentro de un año)
"""
import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from backend.market.zones import ZoneKey, ZoneSpec, normalize_zone_name

HEADER = ["texto", "zona", "nota"]
OUT_OF_SCOPE = "out_of_scope"


class AliasFileError(ValueError):
    """El archivo de alias no es válido. El mensaje trae el número de línea."""


@dataclass(frozen=True)
class AliasSpec:
    text: str                      # tal como está en el archivo
    alias_norm: str
    target: Optional[ZoneKey]      # (level, code) de la zona; None = out_of_scope
    note: str


def parse_target(raw: str, line: int) -> Optional[ZoneKey]:
    raw = raw.strip()
    if raw == OUT_OF_SCOPE:
        return None
    if raw == "ciudad":
        return ("ciudad", None)
    level, _, code = raw.partition(":")
    if level not in ("distrito", "barrio") or not code.strip().isdigit():
        raise AliasFileError(f"línea {line}: zona {raw!r} no válida (use distrito:20, barrio:026, ciudad u {OUT_OF_SCOPE})")
    return (level, code.strip())


def parse_aliases_text(content: str, specs: Iterable[ZoneSpec]) -> list[AliasSpec]:
    """Parsea y valida contra las zonas oficiales `specs`: el destino debe existir, el texto no puede
    repetirse ni coincidir con el nombre oficial de ninguna zona, y la nota es obligatoria."""
    specs = list(specs)
    zones = {s.key: s for s in specs}
    official = {s.name_norm: s for s in specs}
    reader = csv.reader(io.StringIO(content.lstrip("﻿"), newline=""))
    result: list[AliasSpec] = []
    seen: dict[str, int] = {}
    for fields in reader:
        line = reader.line_num
        if not fields or all(not f.strip() for f in fields):
            continue
        if line == 1:
            if [f.strip() for f in fields] != HEADER:
                raise AliasFileError(f"línea 1: cabecera inesperada {fields!r}; se esperaba {HEADER!r}")
            continue
        if len(fields) != len(HEADER):
            raise AliasFileError(f"línea {line}: {len(fields)} columnas, se esperaban {len(HEADER)}")
        text, target_raw, note = (f.strip() for f in fields)
        norm = normalize_zone_name(text)
        if not norm:
            raise AliasFileError(f"línea {line}: texto vacío")
        if not note:
            raise AliasFileError(f"línea {line}: falta la nota para {text!r}")
        target = parse_target(target_raw, line)
        if target is not None and target not in zones:
            raise AliasFileError(f"línea {line}: la zona {target_raw!r} no existe en el CSV registral")
        if norm in seen:
            raise AliasFileError(f"línea {line}: {text!r} repite el alias de la línea {seen[norm]}")
        seen[norm] = line
        owner = official.get(norm)
        if owner is not None:
            raise AliasFileError(f"línea {line}: {text!r} ya es el nombre oficial de {owner.name!r} (alias 'official'); sobra")
        result.append(AliasSpec(text, norm, target, note))
    return result


def load_aliases_csv(path: str | Path, specs: Iterable[ZoneSpec]) -> list[AliasSpec]:
    return parse_aliases_text(Path(path).read_text(encoding="utf-8"), specs)
