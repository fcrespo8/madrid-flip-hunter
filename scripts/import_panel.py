"""Importa nueve indicadores del Panel de indicadores de distritos y barrios (datos.madrid.es) a zone_indicators.

Indicadores (lista fija, por nombre normalizado): renta neta del hogar, valor catastral medio, año medio de construcción,
tasa de paro, crecimiento de población, tamaño medio del hogar, % de envejecimiento, locales cerrados y densidad.
Si alguno desaparece o cambia de nombre, el script avisa, sugiere nombres parecidos y NO escribe nada (salvo --allow-missing).
Cada carga queda en market_imports; el mismo archivo dos veces no hace nada; solo se agregan valores nuevos o revisados.

Uso (dry-run por defecto; descarga el CSV de datos.madrid.es, calcula qué haría y descarta los cambios):
    poetry run python -m scripts.import_panel
    poetry run python -m scripts.import_panel --file acumulado.csv       # sin red
Para escribir:
    poetry run python -m scripts.import_panel --confirm

Requiere la migración c0d5f8b2e4a6 y las zonas sembradas (scripts/seed_zones.py). Con DATABASE_URL apuntando a Railway,
el guard de database.py exige ENV=production.
"""
import argparse
import sys
from pathlib import Path

from backend.market.panel import (
    ALREADY_IMPORTED, INDICATORS, PanelFetchError, PanelImportError, PanelIndicatorMissing, PanelParseError,
    fetch_panel, import_panel,
)
from backend.models.database import SessionLocal, engine

EXIT_BAD_INPUT = 2
EXIT_FETCH = 3
EXIT_MISSING = 4


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", type=Path, default=None, help="CSV acumulado ya descargado (sin red)")
    parser.add_argument("--confirm", action="store_true", help="escribe los cambios (sin esto es un dry-run)")
    parser.add_argument("--allow-missing", action="store_true", help="sigue aunque falte algún indicador de la lista")
    return parser.parse_args(argv)


def main(argv=None, http=None) -> int:
    args = parse_args(argv)
    try:
        if args.file:
            content, origin = args.file.read_bytes(), args.file.name
        else:
            content, origin = fetch_panel(**({"http": http} if http else {}))
    except PanelFetchError as e:
        print(f"ERROR: {e}. No se escribió nada.")
        return EXIT_FETCH
    except OSError as e:
        print(f"ERROR leyendo {args.file}: {e}")
        return EXIT_BAD_INPUT

    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    file_name = origin.rsplit("/", 1)[-1]
    db = SessionLocal()
    try:
        result = import_panel(db, content, file_name, allow_missing=args.allow_missing)
        if result.status == ALREADY_IMPORTED:
            db.rollback()
            print(f"ya importado (carga #{result.import_id}, sha256 {result.sha256[:12]}…): no se hace nada")
            return 0
        print(f"origen: {origin} · sha256 {result.sha256[:12]}… · años {result.period_from}–{result.period_to}")
        for spec in INDICATORS:
            info = result.per_indicator.get(spec.key)
            if info is None:
                print(f"  ✗ {spec.key:24s} FALTA ← {spec.panel_name!r}")
                for similar in result.suggestions.get(spec.key, []):
                    print(f"      ¿será {similar!r}?")
                continue
            print(f"  ✓ {spec.key:24s} {info['rows']:5d} valores · {info['years'][0]}–{info['years'][1]} · {info['barrios']:3d} barrios · "
                  f"{info['inserted']:5d} a insertar")
        print(f"valores leídos: {result.values_read} · a insertar: {result.inserted} ({result.revised} revisan un valor ya cargado) · "
              f"sin cambios: {result.unchanged} · reemplazados por una edición más nueva: {result.superseded}")
        if result.missing:
            print(f"AVISO: faltan {len(result.missing)} indicadores (se siguió por --allow-missing)")
        if not args.confirm:
            db.rollback()
            print("[dry-run] no se escribió nada. Para aplicar: --confirm")
            return 0
        db.commit()
        print(f"OK: carga #{result.import_id} registrada, {result.inserted} valores insertados")
        return 0
    except PanelIndicatorMissing as e:
        db.rollback()
        print(f"ABORTADO: {e}")
        for spec in e.missing:
            for similar in e.suggestions.get(spec.key, []):
                print(f"  {spec.key}: ¿será {similar!r}?")
        print("Actualizá la lista en backend/market/panel.py o usá --allow-missing. No se escribió nada.")
        return EXIT_MISSING
    except (PanelImportError, PanelParseError) as e:
        db.rollback()
        print(f"ERROR: {e}")
        return EXIT_BAD_INPUT
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
