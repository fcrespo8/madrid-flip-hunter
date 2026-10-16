"""Rellena pre_score, discount_pct y market_zone en los listings que todavía no los tienen.

Las columnas las escribe el pre-scorer en cada corrida, pero solo sobre los listings que procesa
(los pendientes). Los que ya estaban puntuados antes de la migración quedan en NULL: este script
les calcula esos tres campos con la misma lógica y NO toca score, score_status ni nada más.

Uso (dry-run por defecto; no escribe nada):
    ENV=production poetry run python -m scripts.backfill_pre_scores
Para escribir:
    ENV=production poetry run python -m scripts.backfill_pre_scores --confirm
Con --all recalcula también los que ya tienen valores (p. ej. tras actualizar la tabla de precios).
"""
import argparse
import sys
from collections import Counter

from sqlalchemy import or_

from backend.agents.pre_scorer import pre_score_fields
from backend.models.database import SessionLocal, engine
from backend.models.listing import Listing


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--confirm", action="store_true", help="escribe los cambios (sin esto es un dry-run)")
    parser.add_argument("--all", action="store_true", help="recalcula también los listings que ya tienen valores")
    return parser.parse_args(argv)


def target_query(db, recalc_all: bool):
    query = db.query(Listing)
    if not recalc_all:
        query = query.filter(or_(Listing.pre_score.is_(None), Listing.discount_pct.is_(None), Listing.market_zone.is_(None)))
    return query.order_by(Listing.id)


def main(argv=None) -> int:
    args = parse_args(argv)
    print(f"DB: {engine.url.render_as_string(hide_password=True)}")
    db = SessionLocal()
    try:
        changed, summary = 0, Counter()
        for listing in target_query(db, args.all).all():
            fields = pre_score_fields(listing)
            summary["con pre-score" if fields.pre_score is not None else "sin pre-score"] += 1
            if (listing.pre_score, listing.discount_pct, listing.market_zone) == \
                    (fields.pre_score, fields.discount_pct, fields.market_zone):
                continue
            changed += 1
            if args.confirm:
                listing.pre_score, listing.discount_pct, listing.market_zone = \
                    fields.pre_score, fields.discount_pct, fields.market_zone
        print(f"revisados: {sum(summary.values())} · a actualizar: {changed} · {dict(summary)}")
        if not args.confirm:
            print("[dry-run] no se escribió nada. Para aplicar: --confirm")
            db.rollback()
            return 0
        db.commit()
        print(f"OK: {changed} listings actualizados")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
