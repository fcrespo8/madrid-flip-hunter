"""Cobertura del resolvedor de zonas sobre los anuncios activos. SOLO LECTURA.

Pasa cada anuncio activo (no rechazado por QA) por el resolvedor y muestra:
  - antes: solo con los nombres oficiales (los 153 alias 'official');
  - después: sumando data/aliases.csv;
  - los textos que siguen sin resolver, por frecuencia.

El resolvedor se arma en memoria desde el CSV registral y el archivo de alias: no hace falta que las tablas
zones/zone_aliases existan en la base. La base se usa solo para leer listings, en una transacción READ ONLY.

Uso:
    ENV=production poetry run python -m scripts.zone_coverage
    ENV=production poetry run python -m scripts.zone_coverage --top 60 --include-rejected
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

from backend.market.aliases import AliasFileError, load_aliases_csv
from backend.market.coverage import CoverageReport, ListingLocation, coverage_report
from backend.market.registral import RegistralParseError, load_registral_csv
from backend.market.resolver import resolver_from_specs
from backend.market.zones import ZoneSeedError, zone_specs_from_rows
from backend.models.database import SessionLocal, engine
from backend.models.listing import Listing

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "raw" / "precio_registral_barrio.csv"
DEFAULT_ALIASES = ROOT / "data" / "aliases.csv"
TARGET = 95.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES)
    parser.add_argument("--top", type=int, default=40, help="cuántos textos sin resolver mostrar")
    parser.add_argument("--include-rejected", action="store_true", help="incluye los rechazados por QA")
    return parser.parse_args(argv)


def fetch_locations(include_rejected: bool) -> list[ListingLocation]:
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        query = db.query(Listing.id, Listing.source, Listing.neighborhood, Listing.district).filter(Listing.is_active.is_(True))
        if not include_rejected:
            query = query.filter(Listing.qa_rejected.is_(False))
        rows = [ListingLocation(*row) for row in query.order_by(Listing.id)]
        db.rollback()
        return rows
    finally:
        db.close()


def summary_line(label: str, r: CoverageReport) -> str:
    return (f"{label:8s} con zona {r.matched:5d} ({r.zone_coverage:5.1f} %) · fuera de alcance {r.out_of_scope:4d} · "
            f"sin resolver {r.unresolved:4d} · reconocidos {r.recognized_coverage:5.1f} %")


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        specs = zone_specs_from_rows(load_registral_csv(args.csv))
        aliases = load_aliases_csv(args.aliases, specs)
    except (RegistralParseError, ZoneSeedError, AliasFileError, OSError) as e:
        print(f"ERROR: {e}")
        return 2

    print(f"DB: {engine.url.render_as_string(hide_password=True)} (solo lectura)")
    locations = fetch_locations(args.include_rejected)
    before = coverage_report(resolver_from_specs(specs), locations)
    after = coverage_report(resolver_from_specs(specs, aliases), locations)

    print(f"anuncios activos{'' if args.include_rejected else ' no rechazados por QA'}: {after.total} · "
          f"con ubicación: {after.with_location} · sin ubicación: {after.without_location}")
    print(f"alias manuales en {args.aliases.name}: {len(aliases)} "
          f"({sum(1 for a in aliases if a.target is None)} fuera de alcance)")
    print(summary_line("ANTES", before))
    print(summary_line("DESPUÉS", after))
    print(f"niveles (después): {dict(after.by_level)}")
    print("por fuente (después): " + " · ".join(
        f"{src} {c['matched']}/{sum(c.values())}" for src, c in sorted(after.by_source.items())))
    ok = after.zone_coverage >= TARGET
    print(f"meta {TARGET:.0f} % con zona: {'CUMPLIDA' if ok else 'NO cumplida'} ({after.zone_coverage:.1f} %)")

    print(f"\nsin resolver ({after.unresolved} anuncios, {len(after.unresolved_texts)} textos distintos), top {args.top}:")
    for (neighborhood, district), n in after.unresolved_texts.most_common(args.top):
        ex_id, ex_src = after.unresolved_examples[(neighborhood, district)]
        print(f"  {n:4d} · barrio={neighborhood!r} distrito={district!r}  (ej. id {ex_id}, {ex_src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
