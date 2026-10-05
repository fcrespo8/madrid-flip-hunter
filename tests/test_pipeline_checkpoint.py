"""Checkpointer del grafo del pipeline: retomar sin re-ejecutar lo ya hecho.

Offline con InMemorySaver (nodos reemplazados por fakes que cuentan llamadas) y
un test con AsyncPostgresSaver contra TEST_DATABASE_URL (solo Postgres; corre en CI).
"""
import asyncio
from collections import Counter

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tests.conftest import TEST_DATABASE_URL, Boom, requires_test_db
from tests.test_scoring import VALID_RESULT, FakeClient, _offline, no_docs_retriever  # noqa: F401


def _run(saver, **kwargs):
    from backend.pipeline.graph import run_pipeline
    kwargs.setdefault("scrapers", {"fake": object})
    return asyncio.run(run_pipeline(["fake"], checkpointer=saver, **kwargs))


def _thread_ids(saver):
    return {cp.config["configurable"]["thread_id"] for cp in saver.list(None)}


BEFORE_PRE_SCORE = ("scrape", "deactivate_stale", "select_pending", "qa", "enrich_location", "enrich_sizes")


def test_retomar_no_reejecuta_nodos_previos(fake_nodes):
    calls, fail_once = fake_nodes
    saver = InMemorySaver()
    fail_once.add("pre_score")

    with pytest.raises(Boom):
        _run(saver, run_id="r1")
    assert all(calls[n] == 1 for n in BEFORE_PRE_SCORE) and calls["pre_score"] == 1
    assert calls["notify"] == 0

    state = _run(saver, run_id="r1", resume=True)

    assert all(calls[n] == 1 for n in BEFORE_PRE_SCORE), "los nodos previos no se re-ejecutan"
    assert calls["pre_score"] == 2                     # el que falló se reintenta
    assert sorted(state["scored_ids"]) == [1, 2, 3]
    assert state["notified_ids"] == [1, 2, 3] and calls["finalize"] == 1
    assert state["counts"] == {"located": 3, "sized": 1}       # merge de los dos enrich en paralelo


def test_retomar_solo_repite_el_score_one_que_fallo(fake_nodes):
    calls, fail_once = fake_nodes
    saver = InMemorySaver()
    fail_once.add("score_one:2")

    with pytest.raises(Boom):
        _run(saver, run_id="r2")
    state = _run(saver, run_id="r2", resume=True)

    assert calls["score_one:1"] == 1 and calls["score_one:3"] == 1, "los Send que terminaron no se repiten"
    assert calls["score_one:2"] == 2
    assert calls["pre_score"] == 1
    assert sorted(state["scored_ids"]) == [1, 2, 3]    # sin duplicados por el reducer operator.add


def test_corrida_ok_borra_su_thread(fake_nodes):
    calls, _ = fake_nodes
    saver = InMemorySaver()
    _run(saver, run_id="r3")
    assert "r3" not in _thread_ids(saver)
    before = dict(calls)

    with pytest.raises(ValueError, match="No hay checkpoint"):     # terminada: no hay nada que retomar
        _run(saver, run_id="r3", resume=True)
    assert dict(calls) == before


def test_corrida_fallida_conserva_su_thread(fake_nodes):
    _, fail_once = fake_nodes
    saver = InMemorySaver()
    fail_once.add("notify")
    with pytest.raises(Boom):
        _run(saver, run_id="r3b")
    assert "r3b" in _thread_ids(saver)
    _run(saver, run_id="r3b", resume=True)
    assert "r3b" not in _thread_ids(saver)                         # al terminar el resume, se borra


def test_run_id_existente_sin_resume_falla(fake_nodes):
    _, fail_once = fake_nodes
    saver = InMemorySaver()
    fail_once.add("notify")
    with pytest.raises(Boom):
        _run(saver, run_id="r4")
    with pytest.raises(ValueError, match="ya existe"):
        _run(saver, run_id="r4")


def test_resume_sin_checkpoint_falla(fake_nodes):
    with pytest.raises(ValueError, match="No hay checkpoint"):
        _run(InMemorySaver(), run_id="nunca-corrio", resume=True)
    with pytest.raises(ValueError, match="necesita el run_id"):
        _run(InMemorySaver(), resume=True)


def test_objetos_de_configurable_no_van_al_checkpoint(fake_nodes):
    """scrapers / cliente / retriever no son serializables: LangGraph no los copia
    a la metadata, el checkpoint entero pasa por el serializer del saver (el mismo
    que usa Postgres) y las claves del PipelineState son JSON puro."""
    import json
    import typing
    from backend.pipeline.state import PipelineState
    state_keys = set(typing.get_type_hints(PipelineState))
    calls, fail_once = fake_nodes
    saver = InMemorySaver()
    fail_once.add("finalize")      # que falle al final, para que el thread quede y se pueda inspeccionar
    with pytest.raises(Boom):
        _run(saver, run_id="r5", scrapers={"fake": object}, scoring_client=FakeClient(tool_input=VALID_RESULT),
             retriever=no_docs_retriever, fetch_html=lambda url: None)

    checkpoints = list(saver.list({"configurable": {"thread_id": "r5"}}))
    assert checkpoints
    for cp in checkpoints:
        assert not {"scrapers", "scoring_client", "retriever", "fetch_html"} & set(cp.metadata)
        json.dumps(cp.metadata)
        assert saver.serde.loads_typed(saver.serde.dumps_typed(cp.checkpoint)) == cp.checkpoint
        json.dumps({k: v for k, v in cp.checkpoint["channel_values"].items() if k in state_keys})


@pytest.mark.parametrize("url, expected", [
    ("postgresql://u:p@localhost:5432/flip_test", "postgresql://u:p@localhost:5432/flip_test"),
    ("postgresql+psycopg2://u:p@db.example.com/flip", "postgresql://u:p@db.example.com/flip"),
    ("postgresql+asyncpg://u@h/x", "postgresql://u@h/x"),
])
def test_conn_string_sin_driver(url, expected):
    from backend.pipeline.graph import checkpointer_conn_string
    assert checkpointer_conn_string(url) == expected


def test_conn_string_rechaza_no_postgres():
    from backend.pipeline.graph import checkpointer_conn_string
    with pytest.raises(ValueError, match="Postgres"):
        checkpointer_conn_string("sqlite:///x.db")


# ── AsyncPostgresSaver real (CI) ──────────────────────────────────────────────

requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"), reason="el checkpointer necesita Postgres"
)


@requires_test_db
@requires_postgres
def test_postgres_saver_retoma_despues_de_un_fallo(monkeypatch):
    """Pipeline real contra la DB de test, con AsyncPostgresSaver (+ .setup()):
    pre_score falla la 1ª vez; al retomar, scrape no vuelve a correr."""
    import psycopg
    from backend.agents.market_prices import get_market_price
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    from backend.pipeline import nodes
    from backend.pipeline.graph import checkpointer_conn_string, run_pipeline
    from backend.scrapers import run_scrapers
    from backend.scrapers.base_scraper import RawListing

    src, run_id = "pytest-checkpoint", "pytest-checkpoint-run"
    market = get_market_price("Lavapiés", "Centro")
    raws = [RawListing(source=src, external_id="c1", url="https://example.com/c1", title="Piso a reformar",
                       price=market * 50 * 0.65, size_m2=50.0, rooms=2, neighborhood="Lavapiés",
                       district="Centro", lat=None, lon=None, description=None)]
    scrape_calls = Counter()

    class FakeScraper:
        async def run(self):
            scrape_calls["run"] += 1
            return raws

    async def no_send(listings):
        pass

    real_pre_score, failed = nodes.pre_score, []

    def flaky_pre_score(state):
        if not failed:
            failed.append(True)
            raise Boom("corte simulado")
        return real_pre_score(state)

    monkeypatch.setattr(nodes, "pre_score", flaky_pre_score)
    monkeypatch.setattr(run_scrapers, "send_whatsapp_alerts", no_send)
    async def no_page(url):
        return None

    kwargs = dict(scrapers={"fake": FakeScraper}, scoring_client=FakeClient(tool_input=VALID_RESULT),
                  retriever=no_docs_retriever, fetch_html=no_page)
    try:
        with pytest.raises(Boom):
            asyncio.run(run_pipeline(["fake"], run_id=run_id, **kwargs))
        state = asyncio.run(run_pipeline(run_id=run_id, resume=True, **kwargs))

        assert scrape_calls["run"] == 1, "scrape no se re-ejecuta al retomar"
        assert len(state["scored_ids"]) == 1 and state["candidate_ids"] == state["scored_ids"]
        with psycopg.connect(checkpointer_conn_string(TEST_DATABASE_URL)) as conn:
            left = conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (run_id,)).fetchone()[0]
        assert left == 0, "al terminar bien, el thread se borra"
    finally:
        with psycopg.connect(checkpointer_conn_string(TEST_DATABASE_URL), autocommit=True) as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (run_id,))
        db = SessionLocal()
        db.query(Listing).filter(Listing.source == src).delete()
        db.commit()
        db.close()


def test_alembic_ignora_tablas_del_checkpointer():
    """Carga include_object de alembic/env.py sin ejecutar las migraciones."""
    import ast
    import sqlalchemy as sa

    src = open("alembic/env.py").read()
    module = ast.parse(src)
    keep = [n for n in module.body
            if isinstance(n, ast.FunctionDef) and n.name == "include_object"
            or isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "LANGGRAPH_CHECKPOINT_TABLES"
                                                   for t in n.targets)]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "alembic/env.py", "exec"), ns)
    include = ns["include_object"]

    md = sa.MetaData()
    for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations", "listings"):
        t = sa.Table(name, md, sa.Column("thread_id", sa.Text), sa.Index(f"ix_{name}", "thread_id"))
        expected = name == "listings"
        assert include(t, name, "table", True, None) is expected
        assert include(t.c.thread_id, "thread_id", "column", True, None) is expected
        assert include(next(iter(t.indexes)), f"ix_{name}", "index", True, None) is expected


@requires_test_db
@requires_postgres
def test_postgres_prune_borra_threads_viejos():
    from datetime import datetime, timedelta, timezone
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from backend.pipeline.graph import checkpointer_conn_string, prune_old_threads

    async def scenario():
        async with AsyncPostgresSaver.from_conn_string(checkpointer_conn_string(TEST_DATABASE_URL)) as saver:
            await saver.setup()
            now = datetime.now(timezone.utc)
            for thread_id, age in (("pytest-prune-viejo", 10), ("pytest-prune-nuevo", 1)):
                cp = empty_checkpoint()
                cp["ts"] = (now - timedelta(days=age)).isoformat()
                await saver.aput({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}, cp,
                                 {"source": "input", "step": -1, "parents": {}}, {})
            try:
                deleted = await prune_old_threads(saver, now=now)
                remaining = {c.config["configurable"]["thread_id"] async for c in saver.alist(None)}
            finally:
                await saver.adelete_thread("pytest-prune-nuevo")
                await saver.adelete_thread("pytest-prune-viejo")
            return deleted, remaining

    deleted, remaining = asyncio.run(scenario())
    assert "pytest-prune-viejo" in deleted and "pytest-prune-viejo" not in remaining
    assert "pytest-prune-nuevo" in remaining
