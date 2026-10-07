"""scripts/reactivate_stale.py: dry-run por defecto, --confirm exige --expect, y solo toca la ventana."""
from datetime import datetime

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from tests.conftest import requires_test_db
from tests.test_scoring import _offline  # noqa: F401


def _sql(conditions):
    from backend.models.listing import Listing
    q = Session().query(Listing).filter(*conditions)
    return str(q.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_defaults_documentados():
    from scripts import reactivate_stale as rs
    args = rs.parse_args([])
    assert args.sources == ["tecnocasa", "redpiso", "remax"]            # donpiso: se desactivó el 06/10
    assert args.since == datetime(2026, 5, 30, 7, 3)                     # corte de la restauración del 05/10
    assert args.until == datetime(2026, 9, 7, 7, 0)                      # 07/10 07:00 UTC − 30 días
    assert args.confirm is False and args.expect is None


def test_condiciones_del_update():
    from scripts import reactivate_stale as rs
    sql = _sql(rs.target_conditions(["tecnocasa", "remax"], datetime(2026, 5, 30, 7, 3), datetime(2026, 9, 7, 7, 0)))
    for fragment in ("listings.is_active IS false", "listings.source IN ('tecnocasa', 'remax')",
                     "listings.last_seen_at >= '2026-05-30 07:03:00'", "listings.last_seen_at < '2026-09-07 07:00:00'"):
        assert fragment in sql


def test_confirm_sin_expect_es_un_error_de_uso():
    from scripts import reactivate_stale as rs
    with pytest.raises(SystemExit) as exc:
        rs.parse_args(["--confirm"])
    assert exc.value.code == 2


# ── Contra DB real (SQLite local / Postgres en CI) ─────────────────────────────

A, B, C = "pytest-react-a", "pytest-react-b", "pytest-react-c"


@pytest.fixture
def seeded():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing

    def add(db, src, key, active, seen):
        db.add(Listing(source=src, external_id=key, url=key, title=key, price=1.0, is_active=active,
                       last_seen_at=seen))

    db = SessionLocal()
    add(db, A, "dentro-1", False, datetime(2026, 6, 29, 7, 0))
    add(db, A, "dentro-2", False, datetime(2026, 6, 1, 7, 0))
    add(db, A, "dentro-borde", False, datetime(2026, 5, 30, 7, 3))           # since es inclusive
    add(db, A, "antes", False, datetime(2026, 5, 30, 7, 2, 59))             # inactivo desde antes del 05/10
    add(db, A, "despues", False, datetime(2026, 9, 7, 7, 0))                # until es exclusivo
    add(db, A, "activo", True, datetime(2026, 6, 29, 7, 0))
    add(db, B, "dentro-1", False, datetime(2026, 6, 29, 7, 0))
    add(db, C, "otra-fuente", False, datetime(2026, 6, 29, 7, 0))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(Listing).filter(Listing.source.in_([A, B, C])).delete()
    db.commit()
    db.close()


def active_map():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()
    try:
        return {(x.source, x.external_id): x.is_active
                for x in db.query(Listing).filter(Listing.source.in_([A, B, C]))}
    finally:
        db.close()


ARGS = ["--sources", A, B]


@requires_test_db
def test_dry_run_no_escribe_y_cuenta_por_fuente(seeded, capsys):
    from scripts import reactivate_stale as rs
    before = active_map()
    assert rs.main(ARGS) == 0
    out = capsys.readouterr().out
    assert f"{A}: 3" in out and f"{B}: 1" in out and "a reactivar: 4" in out
    assert "[dry-run]" in out and "--confirm --expect 4" in out
    assert active_map() == before


@requires_test_db
def test_confirm_con_expect_distinto_aborta_sin_cambios(seeded, capsys):
    from scripts import reactivate_stale as rs
    before = active_map()
    assert rs.main(ARGS + ["--confirm", "--expect", "5"]) == rs.EXIT_MISMATCH
    assert "ABORTADO" in capsys.readouterr().out
    assert active_map() == before


@requires_test_db
def test_confirm_reactiva_solo_la_ventana(seeded, capsys):
    from scripts import reactivate_stale as rs
    assert rs.main(ARGS + ["--confirm", "--expect", "4"]) == 0
    assert "OK: 4 listings reactivados" in capsys.readouterr().out
    assert active_map() == {
        (A, "dentro-1"): True, (A, "dentro-2"): True, (A, "dentro-borde"): True,   # reactivados
        (B, "dentro-1"): True,
        (A, "antes"): False,        # inactivo desde antes de la restauración del 05/10
        (A, "despues"): False,      # visto después del corte de esa corrida
        (A, "activo"): True,        # ya estaba activo
        (C, "otra-fuente"): False,  # fuente fuera de --sources
    }


@requires_test_db
def test_confirm_es_idempotente_y_no_repite(seeded, capsys):
    from scripts import reactivate_stale as rs
    assert rs.main(ARGS + ["--confirm", "--expect", "4"]) == 0
    assert rs.main(ARGS + ["--confirm", "--expect", "4"]) == rs.EXIT_MISMATCH     # ahora hay 0: no coincide
    assert "no coincide" in capsys.readouterr().out
