"""Parte C1: enrich_sizes en el grafo, scheduler → run_pipeline, limpieza de
checkpoints, max_llm_calls y sources. Offline salvo los marcados requires_test_db."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from tests.conftest import Boom, requires_test_db
from tests.test_pipeline_checkpoint import _run, _thread_ids
from tests.test_scoring import VALID_RESULT, FakeClient, FakeDB, FakeQuery, _offline, make_listing  # noqa: F401
from backend.scrapers.base_scraper import ScrapeResult


def _html(m2):
    return f"<div>Superficie: {m2} m²</div>"


# ── 1. enrich_sizes ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("m2, ok", [(14.9, False), (15, True), (62.5, True), (1000, True), (1000.5, False)])
def test_regla_15_a_1000(m2, ok):
    from backend.agents.enrich_size import is_valid_size
    assert is_valid_size(m2) is ok


def test_enrich_listing_sizes_guarda_rechaza_y_resetea():
    from backend.agents.enrich_size import enrich_listing_sizes
    no_size = make_listing(id=1, url="u1", size_m2=None, score_status="unscorable", score_status_reason="no_size")
    pendiente = make_listing(id=2, url="u2", size_m2=None)
    chico = make_listing(id=3, url="u3", size_m2=None)
    sin_dato = make_listing(id=4, url="u4", size_m2=None)
    roto = make_listing(id=5, url="u5", size_m2=None)
    pages = {"u1": _html(62), "u2": _html(80), "u3": _html(12), "u4": "<p>sin datos</p>"}

    async def fetch(url):
        if url == "u5":
            raise TimeoutError("networkidle")
        return pages[url]

    db = FakeDB()
    counts = asyncio.run(enrich_listing_sizes(db, [no_size, pendiente, chico, sin_dato, roto], fetch))

    assert counts == {"sized": 2, "size_rejected": 1, "size_not_found": 1, "fetch_failed": 1, "unscorable_reset": 1}
    assert (no_size.size_m2, no_size.score_status, no_size.score_status_reason) == (62.0, "pending", None)
    assert (pendiente.size_m2, pendiente.score_status) == (80.0, "pending")
    assert chico.size_m2 is None and sin_dato.size_m2 is None and roto.size_m2 is None
    assert (chico.score_status, chico.score_status_reason) == ("unscorable", "no_size_final")
    assert (sin_dato.score_status, sin_dato.score_status_reason) == ("unscorable", "no_size_final")
    assert (roto.score_status, roto.score_status_reason) == ("pending", None)   # descarga fallida: sin cambios
    assert db.commits == 4 and db.rollbacks == 0


@pytest.mark.parametrize("fetch_result", [TimeoutError("timeout"), ConnectionError("red"), None, ""])
def test_descarga_fallida_no_cuenta_como_intento(fetch_result):
    """Timeout, error de red o página vacía: el listing queda igual y se reintenta otro día."""
    from backend.agents.enrich_size import enrich_listing_sizes, size_enrichment_query
    listing = make_listing(size_m2=None, score_status="unscorable", score_status_reason="no_size")

    async def fetch(url):
        if isinstance(fetch_result, Exception):
            raise fetch_result
        return fetch_result

    db = FakeDB()
    counts = asyncio.run(enrich_listing_sizes(db, [listing], fetch))
    assert counts["fetch_failed"] == 1 and counts["size_not_found"] == 0
    assert (listing.score_status, listing.score_status_reason) == ("unscorable", "no_size")
    assert db.commits == 0
    assert "listings.score_status_reason = 'no_size'" in _sql(size_enrichment_query(Session(), []))


def test_pagina_sin_m2_no_se_reintenta():
    """Página bajada bien pero sin m² → no_size_final, y la consulta de enrich_sizes
    (que solo toma 'no_size') ya no lo selecciona."""
    from backend.agents.enrich_size import NO_SIZE_FINAL, enrich_listing_sizes, size_enrichment_query
    listing = make_listing(size_m2=None, score_status="unscorable", score_status_reason="no_size")

    async def fetch(url):
        return "<p>Piso luminoso, sin superficie publicada</p>"

    asyncio.run(enrich_listing_sizes(FakeDB(), [listing], fetch))
    assert (listing.score_status, listing.score_status_reason) == ("unscorable", NO_SIZE_FINAL)
    sql = _sql(size_enrichment_query(Session(), []))
    assert f"'{NO_SIZE_FINAL}'" not in sql and "score_status_reason = 'no_size'" in sql


def _sql(query):
    return str(query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_consulta_de_enrich_sizes():
    from backend.agents.enrich_size import size_enrichment_query
    sql = _sql(size_enrichment_query(Session(), pending_ids=[4, 9]))
    for fragment in ("listings.size_m2 IS NULL", "listings.is_active IS true", "listings.qa_rejected IS false",
                     "listings.id IN (4, 9)", "listings.score_status = 'unscorable'",
                     "listings.score_status_reason = 'no_size'", "ORDER BY listings.id DESC"):
        assert fragment in sql
    assert "listings.id IS NULL" in _sql(size_enrichment_query(Session(), pending_ids=[]))


def test_nodo_enrich_sizes_pasa_alcance_y_config(monkeypatch):
    from backend.pipeline import nodes
    seen = {}

    async def fake_enrich(**kwargs):
        seen.update(kwargs)
        return {"sized": 1, "size_rejected": 0, "size_not_found": 0, "fetch_failed": 0, "unscorable_reset": 1}

    async def fetch(url):
        return None

    monkeypatch.setattr(nodes, "_enrich_sizes", fake_enrich)
    out = asyncio.run(nodes.enrich_sizes({"pending_ids": [3, 7]},
                                         {"configurable": {"fetch_html": fetch, "max_size_fetches": 5}}))
    assert seen == {"pending_ids": [3, 7], "max_fetches": 5, "fetch_html": fetch}
    assert out == {"counts": {"sized": 1, "size_rejected": 0, "size_not_found": 0, "fetch_failed": 0,
                              "unscorable_reset": 1}}


@requires_test_db
def test_enrich_sizes_contra_db():
    from backend.agents.enrich_size import enrich_sizes
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing

    src = "pytest-c1-size"
    db = SessionLocal()
    rows = {
        "no_size": dict(score_status="unscorable", score_status_reason="no_size"),
        "no_market": dict(score_status="unscorable", score_status_reason="no_market_price"),
        "pend_in": dict(),
        "pend_out": dict(),
        "rechazado": dict(qa_rejected=True, qa_reason="x"),
        "inactivo": dict(is_active=False),
    }
    ids = {}
    for key, extra in rows.items():
        listing = Listing(source=src, external_id=key, url=key, title=key, price=200000.0, **extra)
        db.add(listing)
        db.commit()
        ids[key] = listing.id
    db.close()

    async def fetch(url):
        return _html(70)

    try:
        counts = asyncio.run(enrich_sizes(pending_ids=[ids["pend_in"]], fetch_html=fetch))
        db = SessionLocal()
        got = {x.external_id: x for x in db.query(Listing).filter(Listing.source == src)}
        assert counts["sized"] == 2 and counts["unscorable_reset"] == 1
        assert (got["no_size"].size_m2, got["no_size"].score_status) == (70.0, "pending")
        assert got["pend_in"].size_m2 == 70.0
        for untouched in ("no_market", "pend_out", "rechazado", "inactivo"):
            assert got[untouched].size_m2 is None, untouched
        db.close()
    finally:
        db = SessionLocal()
        db.query(Listing).filter(Listing.source == src).delete()
        db.commit()
        db.close()


# ── 2. Scheduler ───────────────────────────────────────────────────────────────

def _scheduled_job(monkeypatch, legacy):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from backend.api import main
    if legacy:
        monkeypatch.setenv("SCHEDULER_PIPELINE", "legacy")
    else:
        monkeypatch.delenv("SCHEDULER_PIPELINE", raising=False)
    sched = AsyncIOScheduler()
    main.schedule_daily_pipeline(sched)
    (job,) = sched.get_jobs()
    return main, job


def test_scheduler_usa_run_pipeline(monkeypatch):
    main, job = _scheduled_job(monkeypatch, legacy=False)
    assert job.func is main.scheduled_pipeline
    assert (job.max_instances, job.coalesce, job.id) == (1, True, "daily_pipeline")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("7", "0")


def test_scheduler_rollback_a_run_all(monkeypatch):
    main, job = _scheduled_job(monkeypatch, legacy=True)
    assert job.func is main.run_all
    assert (job.max_instances, job.coalesce) == (1, True)


@pytest.mark.parametrize("env, expected", [(None, None), ("25", 25)])
def test_job_programado_pasa_max_llm_calls(monkeypatch, env, expected):
    from backend.api import main
    from backend.pipeline import graph
    seen = {}

    async def fake_run_pipeline(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(graph, "run_pipeline", fake_run_pipeline)
    if env is None:
        monkeypatch.delenv("PIPELINE_MAX_LLM_CALLS", raising=False)
    else:
        monkeypatch.setenv("PIPELINE_MAX_LLM_CALLS", env)
    asyncio.run(main.scheduled_pipeline())
    assert seen == {"max_llm_calls": expected}


# ── 3. Limpieza de checkpoints ─────────────────────────────────────────────────

def _put_thread(saver, thread_id, ts: datetime):
    cp = empty_checkpoint()
    cp["ts"] = ts.isoformat()
    saver.put({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}, cp,
              {"source": "input", "step": -1, "parents": {}}, {})


def test_prune_borra_solo_threads_viejos():
    from backend.pipeline.graph import prune_old_threads
    now = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)
    saver = InMemorySaver()
    _put_thread(saver, "viejo", now - timedelta(days=8))
    _put_thread(saver, "mixto", now - timedelta(days=9))
    _put_thread(saver, "mixto", now - timedelta(days=1))       # su último checkpoint es reciente
    _put_thread(saver, "nuevo", now - timedelta(days=6))

    deleted = asyncio.run(prune_old_threads(saver, now=now))

    assert deleted == ["viejo"]
    assert _thread_ids(saver) == {"mixto", "nuevo"}


def test_cada_corrida_limpia_threads_viejos_ok_o_con_error(fake_nodes):
    _, fail_once = fake_nodes
    saver = InMemorySaver()
    old = datetime.now(timezone.utc) - timedelta(days=30)

    _put_thread(saver, "viejo-1", old)
    _run(saver, run_id="ok")
    assert _thread_ids(saver) == set()                          # 'ok' borrado al terminar, 'viejo-1' por antigüedad

    _put_thread(saver, "viejo-2", old)
    fail_once.add("notify")
    with pytest.raises(Boom):
        _run(saver, run_id="falla")
    assert _thread_ids(saver) == {"falla"}                      # la fallida queda para retomar


def test_error_de_limpieza_no_tapa_el_resultado(fake_nodes, monkeypatch):
    from backend.pipeline import graph
    _, fail_once = fake_nodes

    async def broken_prune(*a, **k):
        raise RuntimeError("DB caída")

    monkeypatch.setattr(graph, "prune_old_threads", broken_prune)
    assert _run(InMemorySaver(), run_id="a")["notified_ids"] == [1, 2, 3]
    fail_once.add("qa")
    with pytest.raises(Boom):                                     # sigue siendo el error original
        _run(InMemorySaver(), run_id="b")


# ── 4. max_llm_calls ───────────────────────────────────────────────────────────

@pytest.fixture
def pre_score_with_candidates(monkeypatch):
    from contextlib import contextmanager
    from backend.pipeline import nodes

    @contextmanager
    def fake_session():
        yield FakeDB()

    result = SimpleNamespace(candidates=[SimpleNamespace(id=i) for i in (11, 12, 13)], auto_ids=[20],
                             unscorable_ids=[30])
    monkeypatch.setattr(nodes, "_session", fake_session)
    monkeypatch.setattr(nodes, "pending_listings_query", lambda db: FakeQuery([]))
    monkeypatch.setattr(nodes, "apply_pre_scores", lambda db, listings: result)
    return nodes


@pytest.mark.parametrize("cap, candidates, deferred", [
    (None, [11, 12, 13], []),
    (2, [11, 12], [13]),
    (0, [], [11, 12, 13]),
    (10, [11, 12, 13], []),
])
def test_pre_score_corta_candidatos(pre_score_with_candidates, cap, candidates, deferred):
    nodes = pre_score_with_candidates
    out = nodes.pre_score({"pending_ids": [11, 12, 13, 20, 30], "max_llm_calls": cap})
    assert (out["candidate_ids"], out["deferred_ids"]) == (candidates, deferred)
    route = nodes.route_to_scoring({"candidate_ids": out["candidate_ids"]})
    if candidates:
        assert [s.arg["listing_id"] for s in route] == candidates   # un Send por candidato, nada más
    else:
        assert route == "notify"                                   # tope 0: ninguna llamada a Claude


def test_max_llm_calls_negativo_falla():
    from backend.pipeline.graph import run_pipeline
    with pytest.raises(ValueError, match="max_llm_calls"):
        asyncio.run(run_pipeline(checkpointer=InMemorySaver(), max_llm_calls=-1))


def test_max_llm_calls_llega_al_state(fake_nodes, monkeypatch):
    from backend.pipeline import nodes
    seen = {}
    original = nodes.pre_score   # ya es el fake del fixture

    def spy(state):
        seen["cap"] = state.get("max_llm_calls")
        return original(state)

    monkeypatch.setattr(nodes, "pre_score", spy)
    _run(InMemorySaver(), run_id="cap", max_llm_calls=2)
    assert seen["cap"] == 2


# ── 5. sources ─────────────────────────────────────────────────────────────────

def _recording_scrapers(called):
    def make(name):
        class Scraper:
            async def run(self):
                called.append(name)
                return ScrapeResult.finish([], reached_end=True)
        return Scraper
    return {name: make(name) for name in ("wallapop", "donpiso", "remax")}


def test_scrape_corre_solo_las_fuentes_pedidas():
    from backend.pipeline import nodes
    called = []
    out = asyncio.run(nodes.scrape({"sources": ["donpiso"]},
                                   {"configurable": {"scrapers": _recording_scrapers(called)}}))
    assert called == ["donpiso"]
    assert out["source_stats"] == {"donpiso": {"new": 0, "dup": 0, "found": 0, "complete": False,
                                               "total_reported": None, "incomplete_reason": "empty"}}


def test_run_pipeline_rechaza_fuente_desconocida():
    from backend.pipeline.graph import run_pipeline
    with pytest.raises(ValueError, match="Fuentes desconocidas: \\['idealistaa'\\]"):
        asyncio.run(run_pipeline(["wallapop", "idealistaa"], checkpointer=InMemorySaver()))


def test_run_pipeline_pasa_sources_al_state(fake_nodes, monkeypatch):
    from backend.pipeline import nodes
    from backend.pipeline.graph import run_pipeline
    seen = {}
    original = nodes.scrape   # ya es el fake del fixture

    async def spy(state, config):
        seen["sources"] = state["sources"]
        return await original(state, config)

    monkeypatch.setattr(nodes, "scrape", spy)
    asyncio.run(run_pipeline(["wallapop"], checkpointer=InMemorySaver(), scrapers=_recording_scrapers([])))
    assert seen["sources"] == ["wallapop"]
