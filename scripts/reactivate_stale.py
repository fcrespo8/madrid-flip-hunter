"""Reactiva los listings que deactivate_stale desactivó en la corrida programada del 07/10/2026.

Por qué hace falta: esa corrida (07:00 UTC) aplicó la regla de 30 días con scrapers que solo
ven las primeras páginas de cada fuente, así que desactivó listings que siguen publicados.

No hay una columna con la fecha de desactivación: los afectados se identifican por fuente y
por `last_seen_at`. Una ventana [since, until) deja afuera:
  - lo desactivado ANTES del 05/10 (last_seen_at < since): quedó inactivo antes de la
    restauración de ese día y no se tocó;
  - todo lo visto después del corte de esa corrida (last_seen_at >= until).
Por defecto: tecnocasa, redpiso y remax. donpiso no entra: sus 14 desactivados fueron el 06/10.

Cifra esperada ≈ 939 (tecnocasa 696, redpiso 157, remax 86): los 971 activos del 06/10 menos
los 32 existentes que esa corrida volvió a ver. Es una deducción de conteos, NO una medición:
confirmala con el dry-run antes de usar --confirm. Puede incluir unos ~9 que se desactivaron
a mano desde el dashboard; no hay forma de distinguirlos.

Uso (dry-run por defecto; no escribe nada):
    ENV=production poetry run python -m scripts.reactivate_stale
Para ejecutar, con el conteo que mostró el dry-run:
    ENV=production poetry run python -m scripts.reactivate_stale --confirm --expect 939

Si el conteo real no coincide con --expect, o el UPDATE no afecta exactamente esa cantidad
de filas, no se modifica nada.
"""
import argparse
import sys
from datetime import datetime

from sqlalchemy import func

from backend.models.database import SessionLocal, engine
from backend.models.listing import Listing

DEFAULT_SOURCES = ["tecnocasa", "redpiso", "remax"]
DEFAULT_SINCE = "2026-05-30 07:03:00"   # corte de la restauración del 05/10
DEFAULT_UNTIL = "2026-09-07 07:00:00"   # 07/10 07:00 UTC menos STALE_DAYS (30): el corte de esa corrida

EXIT_MISMATCH = 3


def target_conditions(sources: list[str], since: datetime, until: datetime) -> list:
    return [
        Listing.is_active.is_(False),
        Listing.source.in_(sources),
        Listing.last_seen_at >= since,
        Listing.last_seen_at < until,
    ]


def count_by_source(db, sources: list[str], since: datetime, until: datetime) -> dict[str, int]:
    rows = (
        db.query(Listing.source, func.count())
        .filter(*target_conditions(sources, since, until))
        .group_by(Listing.source)
        .all()
    )
    found = dict(rows)
    return {src: found.get(src, 0) for src in sources}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", nargs="+", default=DEFAULT_SOURCES)
    parser.add_argument("--since", type=datetime.fromisoformat, default=datetime.fromisoformat(DEFAULT_SINCE),
                        help="last_seen_at mínimo, inclusive (UTC)")
    parser.add_argument("--until", type=datetime.fromisoformat, default=datetime.fromisoformat(DEFAULT_UNTIL),
                        help="last_seen_at máximo, exclusivo (UTC)")
    parser.add_argument("--expect", type=int, help="cantidad que mostró el dry-run; --confirm la exige")
    parser.add_argument("--confirm", action="store_true", help="ejecutar el UPDATE (por defecto, dry-run)")
    args = parser.parse_args(argv)
    if args.confirm and args.expect is None:
        parser.error("--confirm necesita --expect N (la cantidad que mostró el dry-run)")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    print(f"Base: {engine.url.host or 'local'} / {engine.url.database}")
    print(f"Ventana: last_seen_at en [{args.since}, {args.until}) · fuentes: {', '.join(args.sources)}")

    db = SessionLocal()
    try:
        by_source = count_by_source(db, args.sources, args.since, args.until)
        total = sum(by_source.values())
        for src, n in by_source.items():
            print(f"  {src}: {n}")
        print(f"Listings inactivos a reactivar: {total}")

        if not args.confirm:
            print(f"[dry-run] No se modificó nada. Para ejecutar: --confirm --expect {total}")
            return 0

        if total != args.expect:
            print(f"ABORTADO: el conteo real ({total}) no coincide con --expect ({args.expect}). No se modificó nada.")
            return EXIT_MISMATCH

        updated = (
            db.query(Listing)
            .filter(*target_conditions(args.sources, args.since, args.until))
            .update({"is_active": True}, synchronize_session=False)
        )
        if updated != args.expect:
            db.rollback()
            print(f"ABORTADO: el UPDATE afectó {updated} filas, se esperaban {args.expect}. ROLLBACK.")
            return EXIT_MISMATCH
        db.commit()
        print(f"OK: {updated} listings reactivados.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
