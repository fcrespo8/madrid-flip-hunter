"""Tests de la parte A contra una DB real (TEST_DATABASE_URL). En CI corren después
de `alembic upgrade head`, así que también validan la migración b7e2d9a4c1f0."""
import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import requires_test_db
from tests.test_scoring import VALID_RESULT, FakeClient, _offline, no_docs_retriever  # noqa: F401

pytestmark = requires_test_db

SRC = "pytest-pipeline"


@pytest.fixture
def db():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.query(Listing).filter(Listing.source == SRC).delete()
        session.commit()
        session.close()


def add(db, n, **overrides):
    from backend.models.listing import Listing
    data = dict(source=SRC, external_id=f"x{n}", url=f"https://example.com/{n}", title=f"Piso a reformar {n}",
                price=200_000.0, size_m2=50.0, rooms=2, neighborhood="Lavapiés", district="Centro")
    data.update(overrides)
    listing = Listing(**data)
    db.add(listing)
    db.commit()
    return listing


def ours(query):
    from backend.models.listing import Listing
    return [x.id for x in query.filter(Listing.source == SRC).all()]


def test_defaults_de_servidor(db):
    listing = add(db, 1)
    db.refresh(listing)
    assert (listing.score_status, listing.score_attempts, listing.qa_rejected) == ("pending", 0, False)


def test_check_rechaza_estado_invalido(db):
    listing = add(db, 1)
    listing.score_status = "bogus"
    with pytest.raises(IntegrityError):
        db.commit()


def test_qa_marca_y_pendientes_los_excluye(db):
    from backend.agents.qa_agent import QAAgent
    from backend.models.listing import Listing
    from backend.models.repository import pending_listings_query

    ok = add(db, 1)
    alquiler = add(db, 2, title="Piso en alquiler")
    inactivo = add(db, 3, is_active=False)
    puntuado = add(db, 4, score=5.0, score_status="auto")
    ids = (ok.id, alquiler.id, inactivo.id, puntuado.id)

    QAAgent().run(db)
    db.expire_all()

    rechazado = db.get(Listing, alquiler.id)
    assert rechazado is not None, "QA no debe borrar"
    assert rechazado.qa_rejected and rechazado.qa_reason == "es alquiler, no venta"
    assert ours(pending_listings_query(db)) == [ids[0]]


def test_pre_score_persiste_estados(db):
    from backend.agents.market_prices import get_market_price
    from backend.agents.pre_scorer import apply_pre_scores
    from backend.models.listing import Listing
    from backend.models.repository import pending_listings_query

    market = get_market_price("Lavapiés", "Centro")
    candidato = add(db, 1, price=market * 50 * 0.65)
    auto = add(db, 2, price=market * 50)
    sin_m2 = add(db, 3, size_m2=None)

    apply_pre_scores(db, [candidato, auto, sin_m2])
    db.expire_all()

    assert db.get(Listing, auto.id).score_status == "auto"
    no_puntuable = db.get(Listing, sin_m2.id)
    assert (no_puntuable.score_status, no_puntuable.score_status_reason) == ("unscorable", "no_size")
    assert ours(pending_listings_query(db)) == [candidato.id]


def test_runner_persiste_intentos_y_failed(db):
    from backend.models.listing import Listing
    from backend.models.repository import pending_listings_query
    from backend.scoring.runner import run_scoring

    listing = add(db, 1)
    for _ in range(3):
        asyncio.run(run_scoring([listing], db, client=FakeClient(raise_exc=RuntimeError("503")),
                                retriever=no_docs_retriever))
    db.expire_all()

    fallido = db.get(Listing, listing.id)
    assert (fallido.score_attempts, fallido.score_status) == (3, "failed")
    assert fallido.score_status_reason == "LLMCallError: 503"
    assert ours(pending_listings_query(db)) == []


def test_runner_exito_persiste_llm(db):
    from backend.models.listing import Listing
    from backend.scoring.runner import run_scoring

    listing = add(db, 1)
    asyncio.run(run_scoring([listing], db, client=FakeClient(tool_input=VALID_RESULT),
                            retriever=no_docs_retriever))
    db.expire_all()

    guardado = db.get(Listing, listing.id)
    assert (guardado.score, guardado.score_status, guardado.score_attempts) == (8.5, "llm", 1)


def test_reset_selecciona_y_resetea(db):
    from backend.agents.reset_and_rescore import reset_scores, select_targets
    from backend.models.listing import Listing

    fallido = add(db, 1, score_status="failed", score_attempts=3, score_status_reason="x")
    add(db, 2, qa_rejected=True, qa_reason="es alquiler, no venta")
    add(db, 3, size_m2=None, score_status="unscorable", score_status_reason="no_size")
    llm = add(db, 4, score=8.0, score_status="llm")

    targets = [t for t in select_targets(db) if t.source == SRC]
    assert [t.id for t in targets] == [fallido.id, llm.id]

    reset_scores(db, targets)
    db.expire_all()
    for listing_id in (fallido.id, llm.id):
        reseteado = db.get(Listing, listing_id)
        assert (reseteado.score, reseteado.score_status, reseteado.score_attempts) == (None, "pending", 0)


def test_enrich_size_persiste_vuelta_a_pending(db):
    from backend.agents.enrich_size import apply_size
    from backend.models.listing import Listing
    from backend.models.repository import pending_listings_query

    listing = add(db, 1, size_m2=None, score_status="unscorable", score_status_reason="no_size")
    assert ours(pending_listings_query(db)) == []

    apply_size(listing, 62.0)
    db.commit()
    db.expire_all()

    vuelto = db.get(Listing, listing.id)
    assert (vuelto.size_m2, vuelto.score_status, vuelto.score_status_reason) == (62.0, "pending", None)
    assert ours(pending_listings_query(db)) == [listing.id]
