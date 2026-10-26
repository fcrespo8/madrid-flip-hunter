"""Importa data/raw/precio_registral_barrio.csv a market_imports y market_prices.

Cada carga queda registrada (fuente, archivo, sha256, período, filas). Cargar dos veces el mismo archivo no hace
nada; un archivo distinto agrega filas y nunca pisa las anteriores. Los barrios sin dato no se insertan.

Uso (dry-run por defecto; calcula qué haría y descarta los cambios):
    poetry run python -m scripts.import_registral
Para escribir:
    poetry run python -m scripts.import_registral --confirm

Requiere la migración a8b3d6f1e2c7 y las zonas sembradas (scripts/seed_zones.py). Con DATABASE_URL apuntando a
Railway, el guard de database.py exige ENV=production.
"""
import argparse
import sys
from pathlib import Path

from backend.market.importer import ALREADY_IMPORTED, MarketImportError, SOURCE_REGISTRAL_USADAS, import_registral_file
from backend.market.registral import RegistralParseError
from backend.models.database import SessionLocal, engine

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "raw" / "precio_registral_barrio.csv"
EXIT_BAD_INPUT = 2


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--source", default=SOURCE_REGISTRAL_USADAS)
    parser.add_argument("--confirm", action="store_true", help="escribe los cambios (sin esto es un dry-run)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    db = SessionLocal()
    try:
        result = import_registral_file(db, args.csv, args.source)
        if result.status == ALREADY_IMPORTED:
            db.rollback()
            print(f"ya importado (carga #{result.import_id}, sha256 {result.sha256[:12]}…): no se hace nada")
            return 0
        print(f"archivo: {args.csv.name} · sha256 {result.sha256[:12]}… · período {result.period_from:%Y}–{result.period_to:%Y}")
        print(f"filas leídas: {result.rows_read} · a insertar: {result.inserted} (en {result.zones_with_data} zonas) · "
              f"sin dato, no se insertan: {result.skipped_no_data}")
        if not args.confirm:
            db.rollback()
            print("[dry-run] no se escribió nada. Para aplicar: --confirm")
            return 0
        db.commit()
        print(f"OK: carga #{result.import_id} registrada, {result.inserted} precios insertados")
        return 0
    except (MarketImportError, RegistralParseError, OSError) as e:
        db.rollback()
        print(f"ERROR: {e}")
        return EXIT_BAD_INPUT
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
