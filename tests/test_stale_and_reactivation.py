"""deactivate_stale solo toca fuentes scrapeadas bien; save_listing reactiva lo que reaparece."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from tests.conftest import requires_test_db
from tests.test_scoring import FakeDB, _offline  # noqa: F401


def _sql(query):
    return str(query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ── deactivate_stale: alcance por fuente ───────────────────────────────────────

def test_consulta_filtra_por_fuente():
    from backend.agents.deactivate_stale import stale_listings_query
    sql = _sql(stale_listings_query(Session(), ["donpiso", "remax"], datetime(2026, 9, 5)))
    assert "listings.source IN ('donpiso', 'remax')" in sql
    assert "listings.last_seen_at < '2026-09-05 00:00:00'" in sql
    assert "listings.is_active IS true" in sql


@pytest.mark.parametrize("sources", [[], (), set()])
def test_sin_fuentes_no_desactiva_nada(monkeypatch, sources):
    from backend.agents import deactivate_stale as ds

    def must_not_open():
        raise AssertionError("no debería ni abrir sesión")

    monkeypatch.setattr(ds, "SessionLocal", must_not_open)
    assert ds.deactivate_stale(sources) == 0


def test_deactivate_stale_exige_fuentes():
    from backend.agents.deactivate_stale import deactivate_stale
    with pytest.raises(TypeError):
        deactivate_stale()   # sin argumento ya no existe "todas las fuentes"


def test_nodo_usa_solo_fuentes_que_trajeron_listings(monkeypatch):
    from backend.pipeline import nodes
    seen = {}
    monkeypatch.setattr(nodes, "_deactivate_stale", lambda sources: seen.setdefault("sources", sources) and 7)
    state = {"source_stats": {
        "donpiso": {"new": 1, "dup": 4, "found": 5},
        "remax": {"new": 0, "dup": 0, "found": 0},     # devolvió [] sin error: no cuenta
    }}                                                  # wallapop falló: ni aparece en source_stats
    assert nodes.deactivate_stale(state) == {"deactivated_count": 7}
    assert seen["sources"] == ["donpiso"]


def test_nodo_sin_fuentes_ok_pasa_lista_vacia(monkeypatch):
    from backend.pipeline import nodes
    seen = {}

    def fake(sources):
        seen["sources"] = sources
        return 0

    monkeypatch.setattr(nodes, "_deactivate_stale", fake)
    assert nodes.deactivate_stale({"source_stats": {}}) == {"deactivated_count": 0}
    assert seen["sources"] == []


def test_run_all_pasa_solo_fuentes_con_listings(monkeypatch):
    from backend.scrapers import run_scrapers as rs
    from tests.test_scoring import FakeQuery
    seen = {}

    def scraper(name, n):
        class S:
            source_name = name

            async def run(self):
                return [object()] * n
        return S

    class RunAllDB(FakeDB):
        def expire_all(self):
            pass

        def close(self):
            pass

    async def fake_run_scoring(candidates, db):
        return SimpleNamespace(scored_ids=[])

    async def fake_notify(*a):
        pass

    for name, n in (("WallapopScraper", 2), ("DonpisoScraper", 0), ("RemaxScraper", 1),
                    ("RedpisoScraper", 0), ("TecnocasaScraper", 3)):
        monkeypatch.setattr(rs, name, scraper(name.removesuffix("Scraper").lower(), n))
    monkeypatch.setattr(rs, "save_listing", lambda db, raw: (None, False))
    monkeypatch.setattr(rs, "SessionLocal", RunAllDB)
    monkeypatch.setattr(rs, "deactivate_stale", lambda sources: seen.setdefault("sources", sources))
    monkeypatch.setattr(rs.QAAgent, "run", lambda self, db: None)
    monkeypatch.setattr(rs, "enrich_locations", lambda: 0)
    monkeypatch.setattr(rs, "pending_listings_query", lambda db: FakeQuery([]))
    monkeypatch.setattr(rs, "run_scoring", fake_run_scoring)
    monkeypatch.setattr(rs, "notify_scored", fake_notify)

    asyncio.run(rs.run_all())
    assert seen["sources"] == ["wallapop", "remax", "tecnocasa"]


# ── save_listing: reactivación ─────────────────────────────────────────────────

class _OneRowQuery:
    def __init__(self, row):
        self.row = row

    def filter_by(self, **kw):
        return self

    def first(self):
        return self.row


def _raw(**kw):
    from backend.scrapers.base_scraper import RawListing
    data = dict(source="donpiso", external_id="x1", url="u", title="t", price=1.0, size_m2=50.0, rooms=2,
                neighborhood=None, district=None, lat=None, lon=None, description=None)
    data.update(kw)
    return RawListing(**data)


def test_save_listing_reactiva_al_reaparecer():
    from backend.models.listing import Listing
    from backend.models.repository import save_listing
    old_seen = datetime(2026, 6, 29, 7, 0)
    existing = Listing(source="donpiso", external_id="x1", is_active=False, last_seen_at=old_seen)

    class DB(FakeDB):
        def query(self, model):
            return _OneRowQuery(existing)

    db = DB()
    listing, created = save_listing(db, _raw())
    assert created is False and listing is existing
    assert existing.is_active is True and existing.last_seen_at > old_seen
    assert db.commits == 1


# ── Contra DB real (SQLite local / Postgres en CI) ─────────────────────────────

SRC_A, SRC_B = "pytest-stale-a", "pytest-stale-b"


@pytest.fixture
def stale_rows():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()
    old = datetime.utcnow() - timedelta(days=40)
    fresh = datetime.utcnow() - timedelta(days=2)
    for src in (SRC_A, SRC_B):
        for key, seen in (("viejo", old), ("nuevo", fresh)):
            db.add(Listing(source=src, external_id=key, url=key, title=key, last_seen_at=seen, is_active=True))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(Listing).filter(Listing.source.in_([SRC_A, SRC_B])).delete()
    db.commit()
    db.close()


def _active():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()
    try:
        return {(x.source, x.external_id): x.is_active
                for x in db.query(Listing).filter(Listing.source.in_([SRC_A, SRC_B]))}
    finally:
        db.close()


@requires_test_db
def test_db_desactiva_solo_la_fuente_scrapeada(stale_rows):
    from backend.agents.deactivate_stale import deactivate_stale
    assert deactivate_stale([SRC_A]) == 1
    assert _active() == {(SRC_A, "viejo"): False, (SRC_A, "nuevo"): True,
                         (SRC_B, "viejo"): True, (SRC_B, "nuevo"): True}


@requires_test_db
def test_db_sin_fuentes_no_toca_nada(stale_rows):
    from backend.agents.deactivate_stale import deactivate_stale
    assert deactivate_stale([]) == 0
    assert all(_active().values())


@requires_test_db
def test_db_save_listing_reactiva(stale_rows):
    from backend.agents.deactivate_stale import deactivate_stale
    from backend.models.database import SessionLocal
    from backend.models.repository import save_listing
    deactivate_stale([SRC_A])
    db = SessionLocal()
    try:
        _, created = save_listing(db, _raw(source=SRC_A, external_id="viejo"))
    finally:
        db.close()
    assert created is False and _active()[(SRC_A, "viejo")] is True
