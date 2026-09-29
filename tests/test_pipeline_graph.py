"""Tests del grafo del pipeline (backend/pipeline). Los offline corren siempre;
los marcados con requires_test_db corren contra TEST_DATABASE_URL (CI)."""
import asyncio
import operator
import typing

import pytest

from tests.conftest import requires_test_db
from tests.test_scoring import VALID_RESULT, FakeClient, _offline, no_docs_retriever  # noqa: F401

SRC = "pytest-graph"


@pytest.fixture(autouse=True)
def _no_whatsapp(monkeypatch):
    """database.py hace load_dotenv(): con credenciales de Twilio en el .env,
    notify mandaría mensajes reales. Acá se registran en vez de enviarse."""
    from backend.scrapers import run_scrapers
    sent = []

    async def fake_send(listings):
        sent.extend(x.id for x in listings)

    monkeypatch.setattr(run_scrapers, "send_whatsapp_alerts", fake_send)
    return sent


# ── Offline: estructura, reducers y ruteo ─────────────────────────────────────

def test_aristas_del_grafo():
    from backend.pipeline.graph import build_pipeline_graph
    edges = {(e.source, e.target) for e in build_pipeline_graph().get_graph().edges}
    assert edges == {
        ("__start__", "scrape"), ("scrape", "deactivate_stale"), ("deactivate_stale", "select_pending"),
        ("select_pending", "qa"), ("qa", "enrich_location"), ("enrich_location", "pre_score"),
        ("pre_score", "score_one"), ("pre_score", "notify"), ("score_one", "notify"),
        ("notify", "finalize"), ("finalize", "__end__"),
    }


def test_reducers_del_state():
    from backend.pipeline.state import PipelineState, merge_dicts
    hints = typing.get_type_hints(PipelineState, include_extras=True)
    reduced = {k: v.__metadata__[0] for k, v in hints.items() if hasattr(v, "__metadata__")}
    assert reduced == {"counts": merge_dicts, "scored_ids": operator.add,
                       "failed_ids": operator.add, "errors": operator.add}
    assert merge_dicts({"located": 3}, {"sized": 2}) == {"located": 3, "sized": 2}


def test_state_sin_orm_ni_session():
    from backend.pipeline.state import PipelineState
    allowed = {str, int, list, dict}
    for name, hint in typing.get_type_hints(PipelineState).items():
        assert typing.get_origin(hint) in allowed or hint in allowed, name


def test_route_sin_candidatos_va_a_notify():
    from backend.pipeline.nodes import route_to_scoring
    assert route_to_scoring({"candidate_ids": []}) == "notify"


def test_route_un_send_por_candidato():
    from backend.pipeline.nodes import route_to_scoring
    sends = route_to_scoring({"run_id": "r1", "candidate_ids": [4, 9]})
    assert [(s.node, s.arg) for s in sends] == [
        ("score_one", {"run_id": "r1", "listing_id": 4}),
        ("score_one", {"run_id": "r1", "listing_id": 9}),
    ]


def test_finalize_tolera_state_vacio():
    from backend.pipeline.nodes import finalize
    assert finalize({}) == {}


# ── Contra DB real: pipeline de punta a punta con scrapers y Claude falsos ─────

def _raw(n, **overrides):
    from backend.agents.market_prices import get_market_price
    from backend.scrapers.base_scraper import RawListing
    market = get_market_price("Lavapiés", "Centro")
    data = dict(source=SRC, external_id=f"g{n}", url=f"https://example.com/g{n}", title=f"Piso a reformar {n}",
                price=market * 50, size_m2=50.0, rooms=2, neighborhood="Lavapiés", district="Centro",
                lat=None, lon=None, description="Herencia.")
    data.update(overrides)
    return RawListing(**data)


def _scrapers(raws, fail=False):
    class FakeScraper:
        async def run(self):
            return raws

    class BrokenScraper:
        async def run(self):
            raise RuntimeError("bloqueado por el sitio")

    scrapers = {"fake": FakeScraper}
    if fail:
        scrapers["broken"] = BrokenScraper
    return scrapers


@pytest.fixture
def cleanup():
    yield
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()
    try:
        db.query(Listing).filter(Listing.source == SRC).delete()
        db.commit()
    finally:
        db.close()


def _by_external_id():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()
    try:
        return {x.external_id: x for x in db.query(Listing).filter(Listing.source == SRC)}
    finally:
        db.close()


@requires_test_db
def test_pipeline_de_punta_a_punta(cleanup, _no_whatsapp):
    from backend.pipeline.graph import run_pipeline

    raws = [
        _raw(1, price=_raw(0).price * 0.65),      # -35 % → candidato → Claude 8.5 → notifica
        _raw(2),                                   # a precio de mercado → auto
        _raw(3, size_m2=None),                     # sin m² → unscorable
        _raw(4, title="Piso en alquiler"),         # QA lo rechaza
    ]
    fake = FakeClient(tool_input=VALID_RESULT)
    state = asyncio.run(run_pipeline(["fake", "broken"], run_id="t1", scrapers=_scrapers(raws, fail=True),
                                     scoring_client=fake, retriever=no_docs_retriever))
    rows = _by_external_id()
    ids = {k: v.id for k, v in rows.items()}

    assert state["source_stats"] == {"fake": {"new": 4, "dup": 0, "found": 4}}
    assert [(e["node"], e["source"]) for e in state["errors"]] == [("scrape", "broken")]
    assert sorted(state["new_ids"]) == sorted(ids.values())
    assert ids["g4"] in state["qa_rejected_ids"] and ids["g4"] not in state["pending_ids"]
    assert state["candidate_ids"] == [ids["g1"]]
    assert ids["g2"] in state["auto_scored_ids"] and ids["g3"] in state["unscorable_ids"]
    assert state["scored_ids"] == [ids["g1"]] and state["failed_ids"] == []
    assert state["notified_ids"] == [ids["g1"]] and _no_whatsapp == [ids["g1"]]
    assert state["counts"]["located"] >= 3

    assert rows["g1"].score_status == "llm" and rows["g1"].score == 8.5 and rows["g1"].notified_at
    assert rows["g2"].score_status == "auto"
    assert (rows["g3"].score_status, rows["g3"].score_status_reason) == ("unscorable", "no_size")
    assert rows["g4"].qa_rejected and rows["g4"].score_status == "pending"
    assert len(fake.messages.calls) == 1


@requires_test_db
def test_pipeline_segunda_corrida_no_repite_trabajo(cleanup, _no_whatsapp):
    from backend.pipeline.graph import run_pipeline

    raws = [_raw(1, price=_raw(0).price * 0.65)]
    fake = FakeClient(tool_input=VALID_RESULT)
    for _ in range(2):
        state = asyncio.run(run_pipeline(["fake"], scrapers=_scrapers(raws), scoring_client=fake,
                                         retriever=no_docs_retriever))

    assert state["source_stats"] == {"fake": {"new": 0, "dup": 1, "found": 1}}
    assert state["candidate_ids"] == [] and state["scored_ids"] == [] and state["notified_ids"] == []
    assert len(fake.messages.calls) == 1 and len(_no_whatsapp) == 1


@requires_test_db
def test_pipeline_fallo_de_claude_queda_pendiente(cleanup):
    from backend.pipeline.graph import run_pipeline

    raws = [_raw(1, price=_raw(0).price * 0.65)]
    state = asyncio.run(run_pipeline(["fake"], scrapers=_scrapers(raws),
                                     scoring_client=FakeClient(raise_exc=RuntimeError("503")),
                                     retriever=no_docs_retriever))
    row = _by_external_id()["g1"]
    assert state["scored_ids"] == [] and state["failed_ids"] == []      # 1er intento: sigue pending
    assert [(e["node"], e["listing_id"]) for e in state["errors"]] == [("score_one", row.id)]
    assert (row.score_status, row.score_attempts) == ("pending", 1)


@requires_test_db
def test_score_one_omite_listing_ya_puntuado(cleanup):
    from langchain_core.runnables import RunnableConfig
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    from backend.pipeline.nodes import score_one

    db = SessionLocal()
    listing = Listing(source=SRC, external_id="g1", url="u", title="t", price=1.0, score=7.0, score_status="llm")
    db.add(listing)
    db.commit()
    listing_id = listing.id
    db.close()

    fake = FakeClient(tool_input=VALID_RESULT)
    out = asyncio.run(score_one({"run_id": "r", "listing_id": listing_id},
                                RunnableConfig(configurable={"scoring_client": fake})))
    assert out == {} and fake.messages.calls == []
