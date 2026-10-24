"""Siembra zones y zone_aliases desde data/raw/precio_registral_barrio.csv.

153 zonas (la ciudad, 21 distritos y 131 barrios), cada una con su código oficial, nombre, distrito padre y
nombre normalizado, y su nombre oficial como alias. Es idempotente: correrlo de nuevo no duplica nada.

Uso (dry-run por defecto; calcula qué haría y descarta los cambios):
    poetry run python -m scripts.seed_zones
Para escribir:
    poetry run python -m scripts.seed_zones --confirm

Requiere la migración e6f1b3d7c2a5 (`alembic upgrade head`). Con DATABASE_URL apuntando a Railway,
el guard de database.py exige ENV=production.
"""
import argparse
import sys
from pathlib import Path

from backend.market.registral import RegistralParseError, load_registral_csv
from backend.market.zones import ZoneSeedError, seed_zones, zone_specs_from_rows
from backend.models.database import SessionLocal, engine

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "raw" / "precio_registral_barrio.csv"
EXIT_BAD_INPUT = 2


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="CSV registral (default: data/raw/...)")
    parser.add_argument("--confirm", action="store_true", help="escribe los cambios (sin esto es un dry-run)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        specs = zone_specs_from_rows(load_registral_csv(args.csv))
    except (RegistralParseError, ZoneSeedError, OSError) as e:
        print(f"ERROR en {args.csv}: {e}")
        return EXIT_BAD_INPUT

    by_level = {lvl: sum(1 for s in specs if s.level == lvl) for lvl in ("ciudad", "distrito", "barrio")}
    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    print(f"zonas en el CSV: {len(specs)} {by_level}")
    db = SessionLocal()
    try:
        result = seed_zones(db, specs)
        print(f"zonas: {result.zones_created} nuevas · {result.zones_updated} actualizadas · "
              f"{result.zones_unchanged} sin cambios · aliases: {result.aliases_created} nuevos"
              + (f" · {result.zones_not_in_source} zonas de la base no están en el CSV (no se tocan)"
                 if result.zones_not_in_source else ""))
        if not args.confirm:
            db.rollback()
            print("[dry-run] no se escribió nada. Para aplicar: --confirm")
            return 0
        db.commit()
        print("OK" if result.changed else "OK: ya estaba sembrado, sin cambios")
        return 0
    except ZoneSeedError as e:
        db.rollback()
        print(f"ABORTADO: {e}")
        return EXIT_BAD_INPUT
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
