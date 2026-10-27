"""Carga el IPV del INE (Comunidad de Madrid) en market_imports y market_indices.

Fuente: tabla 79563 del INE (índices trimestrales por comunidad, base 2025 = 100), de tres maneras:
  - el CSV de la web del INE (--file data/raw/79563.csv: `;`, cp1252);
  - un JSON guardado de la API Tempus (--file respuesta.json);
  - la API Tempus directamente (sin --file; --table cambia la tabla).
Cada carga queda registrada; el mismo contenido dos veces no hace nada; solo se agregan puntos nuevos o revisados,
nunca se pisa.

Uso (dry-run por defecto: muestra qué series reconoció, qué insertaría y el factor resultante, y descarta todo):
    poetry run python -m scripts.load_ipv --file data/raw/79563.csv
    poetry run python -m scripts.load_ipv
Para escribir:
    poetry run python -m scripts.load_ipv --confirm

Requiere la migración b9c4e7a1d3f8. Si la API no responde, sale con error y no escribe nada. Con DATABASE_URL apuntando a
Railway, el guard de database.py exige ENV=production.
"""
import argparse
import sys
from pathlib import Path

from backend.market.ipv import (
    ALREADY_IMPORTED, DEFAULT_TABLE_ID, IPVFetchError, IPVParseError, fetch_tempus, import_ipv, parse_ipv_content,
)
from backend.market.rollforward import IndexNotAvailable, rollforward_factor
from backend.models.database import SessionLocal, engine

EXIT_BAD_INPUT = 2
EXIT_FETCH = 3


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", type=Path, default=None, help="CSV de la web del INE o JSON de Tempus guardado (sin red)")
    parser.add_argument("--table", type=int, default=DEFAULT_TABLE_ID, help="id de la tabla de Tempus")
    parser.add_argument("--nult", type=int, default=20, help="últimos N datos por serie (default 20 = 5 años)")
    parser.add_argument("--confirm", action="store_true", help="escribe los cambios (sin esto es un dry-run)")
    return parser.parse_args(argv)


def main(argv=None, http=None) -> int:
    args = parse_args(argv)
    try:
        if args.file:
            content, name = args.file.read_bytes(), args.file.name
        else:
            content, name = fetch_tempus(args.table, args.nult, **({"http": http} if http else {})), f"tempus:{args.table}"
    except IPVFetchError as e:
        print(f"ERROR: {e}. No se escribió nada.")
        return EXIT_FETCH
    except OSError as e:
        print(f"ERROR leyendo {args.file}: {e}")
        return EXIT_BAD_INPUT

    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    db = SessionLocal()
    try:
        result = import_ipv(db, content, name)
        if result.status == ALREADY_IMPORTED:
            db.rollback()
            print(f"ya importado (carga #{result.import_id}, sha256 {result.sha256[:12]}…): no se hace nada")
            return 0
        parsed = parse_ipv_content(content)
        print(f"formato {parsed.format}; series reconocidas (verificá que sean las de la Comunidad de Madrid):")
        for series, nombre in parsed.series_names.items():
            print(f"  {series:13s} ← {nombre!r}")
        print(f"puntos leídos: {result.points_read} ({result.period_from} … {result.period_to}) · a insertar: {result.inserted} "
              f"({result.revised} revisan un valor ya cargado) · sin cambios: {result.unchanged}")
        try:
            rf = rollforward_factor(db, 2025)
            print(f"factor 2025 → {rf.hasta}: ×{rf.factor:.4f} (índice {rf.index_from:.2f} → {rf.index_to:.2f}, serie {rf.series})")
        except IndexNotAvailable as e:
            print(f"factor 2025 → hoy: no calculable ({e})")
        if not args.confirm:
            db.rollback()
            print("[dry-run] no se escribió nada. Para aplicar: --confirm")
            return 0
        db.commit()
        print(f"OK: carga #{result.import_id} registrada, {result.inserted} puntos insertados")
        return 0
    except IPVParseError as e:
        db.rollback()
        print(f"ERROR: {e}")
        return EXIT_BAD_INPUT
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
