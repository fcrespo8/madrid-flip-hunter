"""Tests offline del módulo backend/scoring. Sin API de Claude, sin DB, sin Langfuse."""
import os
os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest


# ── Fakes ──────────────────────────────────────────────────────────────────────

VALID_RESULT = {
    "score": 8.5,
    "reasoning": "Precio/m² muy por debajo de la media del barrio.",
    "red_flags": ["sin ascensor"],
    "green_flags": ["a reformar", "particular"],
}


class FakeMessages:
    def __init__(self, tool_input=None, raise_exc=None, block_type="tool_use", block_name="score_listing",
                 usage=None):
        self.tool_input = tool_input
        self.raise_exc = raise_exc
        self.block_type = block_type
        self.block_name = block_name
        self.usage = usage or SimpleNamespace(input_tokens=100, output_tokens=50)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_exc:
            raise self.raise_exc
        block = SimpleNamespace(type=self.block_type, name=self.block_name, input=self.tool_input, text="")
        return SimpleNamespace(content=[block], usage=self.usage)


class FakeClient:
    def __init__(self, **kwargs):
        self.messages = FakeMessages(**kwargs)


class FakeDB:
    def __init__(self, fail_commit=False):
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeObservation:
    def __init__(self, log, name):
        self.log, self.name = log, name
        self.updates = []
        self.ended = False

    def start_observation(self, name, **kwargs):
        obs = FakeObservation(self.log, name)
        self.log.append(obs)
        return obs

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self):
        self.ended = True


class FakeLangfuse(FakeObservation):
    def __init__(self):
        super().__init__([], "root")
        self.flushed = False

    def flush(self):
        self.flushed = True


def make_listing(**overrides):
    from backend.models.listing import Listing
    data = dict(
        id=1, source="wallapop", external_id="x1", url="https://example.com/1",
        title="Piso a reformar en Lavapiés", price=200000.0, size_m2=50.0, rooms=2,
        neighborhood="Lavapiés", district="Centro", description="Herencia, urgente.",
        # Defaults de la DB (en un objeto sin insertar, SQLAlchemy no los aplica).
        score_status="pending", score_attempts=0, qa_rejected=False, is_active=True,
    )
    data.update(overrides)
    return Listing(**data)


def no_docs_retriever(db, barrio, distrito, top_k):
    return []


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Garantiza que ningún test pueda llegar a la API real ni a Langfuse."""
    from backend.scoring import client, runner

    def _no_real_client():
        raise AssertionError("un test intentó usar el cliente real de Anthropic")

    monkeypatch.setattr(client, "get_client", _no_real_client)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)


# ── prompt.py ──────────────────────────────────────────────────────────────────

def test_score_tool_campos_requeridos():
    from backend.scoring.prompt import SCORE_TOOL
    assert set(SCORE_TOOL["input_schema"]["required"]) == {"score", "reasoning", "red_flags", "green_flags"}


def test_contexto_con_precio_de_mercado():
    from backend.scoring.prompt import build_listing_context
    ctx = build_listing_context(make_listing(), market_price=5000.0)
    assert ctx.startswith("PISO A EVALUAR:")
    assert "- Precio: 200,000€" in ctx
    assert "- Precio/m²: 4000€/m²" in ctx
    assert "- Precio medio del barrio: 5000€/m² (media del barrio según Idealista abr 2026)" in ctx
    assert "- Diferencia vs mercado: -20.0% vs media" in ctx
    assert "- Descripción: Herencia, urgente." in ctx


def test_contexto_sin_tamanyo_ni_mercado():
    from backend.scoring.prompt import build_listing_context
    ctx = build_listing_context(
        make_listing(size_m2=None, rooms=None, neighborhood=None, description=None),
        market_price=None,
    )
    assert "- Tamaño: desconocidom²" in ctx
    assert "- Precio/m²: desconocido" in ctx
    assert "- Precio medio del barrio: no disponible" in ctx
    assert "- Diferencia vs mercado: no calculable" in ctx
    assert "- Habitaciones: desconocido" in ctx
    assert "- Descripción: Sin descripción" in ctx


def test_mensaje_incluye_contexto_rag():
    from backend.scoring.prompt import build_user_message
    msg = build_user_message(make_listing(), rag_context="\n\nCONTEXTO RAG")
    assert msg.startswith("Evalúa esta oportunidad de flipping:\n\nPISO A EVALUAR:")
    assert msg.endswith("\n\nCONTEXTO RAG")


# ── client.py ──────────────────────────────────────────────────────────────────

def test_validacion_ok():
    from backend.scoring.client import validate_score_result
    r = validate_score_result(VALID_RESULT)
    assert r.score == 8.5
    assert r.green_flags == ["a reformar", "particular"]


@pytest.mark.parametrize("score", [0, 10, 7])
def test_validacion_bordes_de_rango(score):
    from backend.scoring.client import validate_score_result
    assert validate_score_result({**VALID_RESULT, "score": score}).score == float(score)


@pytest.mark.parametrize("bad", [
    {**VALID_RESULT, "score": 11},
    {**VALID_RESULT, "score": -0.5},
    {**VALID_RESULT, "score": "8"},
    {**VALID_RESULT, "score": True},
    {**VALID_RESULT, "reasoning": "  "},
    {**VALID_RESULT, "red_flags": "sin ascensor"},
    {**VALID_RESULT, "green_flags": [1, 2]},
    {k: v for k, v in VALID_RESULT.items() if k != "green_flags"},
    {k: v for k, v in VALID_RESULT.items() if k != "score"},
    None,
])
def test_validacion_rechaza(bad):
    from backend.scoring.client import ScoreValidationError, validate_score_result
    with pytest.raises(ScoreValidationError):
        validate_score_result(bad)


def test_request_fuerza_tool_choice():
    from backend.scoring.client import request_score
    from backend.scoring.prompt import MODEL_ID
    fake = FakeClient(tool_input=VALID_RESULT)
    resp = asyncio.run(request_score("hola", client=fake))
    assert resp.raw == VALID_RESULT
    assert resp.input_tokens == 100
    call = fake.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "score_listing"}
    assert call["model"] == MODEL_ID
    assert call["temperature"] == 0


def test_request_errores_tipados():
    from backend.scoring.client import LLMCallError, NoToolUseError, request_score
    with pytest.raises(LLMCallError):
        asyncio.run(request_score("x", client=FakeClient(raise_exc=RuntimeError("503"))))
    with pytest.raises(NoToolUseError):
        asyncio.run(request_score("x", client=FakeClient(block_type="text", block_name=None)))


# ── rag.py ─────────────────────────────────────────────────────────────────────

def test_rag_formatea_docs():
    from backend.scoring.rag import get_rag_context
    doc = SimpleNamespace(barrio="Lavapiés", distrito="Centro", content="Barrio en gentrificación.")
    ctx, n = get_rag_context(FakeDB(), make_listing(), retriever=lambda *a: [doc])
    assert n == 1
    assert "CONTEXTO CUALITATIVO DEL BARRIO" in ctx
    assert "[Lavapiés - Centro]\nBarrio en gentrificación." in ctx


def test_rag_falla_hace_rollback():
    from backend.scoring.rag import get_rag_context

    def broken(*a):
        raise RuntimeError("pgvector caído")

    db = FakeDB()
    assert get_rag_context(db, make_listing(), retriever=broken) == ("", 0)
    assert db.rollbacks == 1


def test_rag_sin_barrio_no_consulta():
    from backend.scoring.rag import get_rag_context

    def must_not_call(*a):
        raise AssertionError("no debería consultar")

    assert get_rag_context(FakeDB(), make_listing(neighborhood=None), retriever=must_not_call) == ("", 0)


# ── graph.py ───────────────────────────────────────────────────────────────────

def _run_graph(client, db=None, listing=None, lf_span=None):
    from backend.scoring.graph import build_scoring_graph
    graph = build_scoring_graph(client=client, retriever=no_docs_retriever)
    listing = listing or make_listing()
    db = db or FakeDB()
    state = asyncio.run(graph.ainvoke({"listing": listing, "db": db, "_lf_span": lf_span}))
    return state, listing, db


def test_grafo_camino_feliz_guarda():
    state, listing, db = _run_graph(FakeClient(tool_input=VALID_RESULT))
    assert state["saved"] is True
    assert state.get("error") is None
    assert listing.score == 8.5
    assert listing.score_green_flags == "a reformar, particular"
    assert listing.score_red_flags == "sin ascensor"
    assert listing.scored_at is not None
    assert db.commits == 1


def test_grafo_validacion_falla_no_guarda():
    state, listing, db = _run_graph(FakeClient(tool_input={**VALID_RESULT, "score": 42}))
    assert "ScoreValidationError" in state["error"]
    assert not state.get("saved")
    assert listing.score is None
    assert listing.scored_at is None
    assert db.commits == 0


def test_grafo_error_llm_no_valida_ni_guarda():
    state, listing, db = _run_graph(FakeClient(raise_exc=RuntimeError("timeout")))
    assert "LLMCallError" in state["error"]
    assert "result" not in state
    assert listing.score is None
    assert db.commits == 0


def test_grafo_sin_tool_use_no_guarda():
    state, listing, db = _run_graph(FakeClient(block_type="text", block_name=None))
    assert "NoToolUseError" in state["error"]
    assert db.commits == 0


def test_grafo_error_de_commit_hace_rollback():
    state, _, db = _run_graph(FakeClient(tool_input=VALID_RESULT), db=FakeDB(fail_commit=True))
    assert state["saved"] is False
    assert "DBError" in state["error"]
    assert db.rollbacks == 1


def test_grafo_langfuse_cierra_observaciones():
    lf = FakeLangfuse()
    _run_graph(FakeClient(tool_input=VALID_RESULT), lf_span=lf)
    names = [o.name for o in lf.log]
    assert names == ["retrieve_rag", "llm_score"]
    assert all(o.ended for o in lf.log)


# ── runner.py ──────────────────────────────────────────────────────────────────

def test_runner_listings_sin_db_falla():
    from backend.scoring.runner import run_scoring
    with pytest.raises(ValueError):
        asyncio.run(run_scoring(listings=[make_listing()]))


def test_runner_lote_mixto(monkeypatch):
    from backend.scoring import runner

    class SeqMessages(FakeMessages):
        """Primer listing válido, segundo con score fuera de rango."""
        async def create(self, **kwargs):
            self.tool_input = VALID_RESULT if not self.calls else {**VALID_RESULT, "score": -1}
            return await super().create(**kwargs)

    fake = FakeClient()
    fake.messages = SeqMessages()
    lf = FakeLangfuse()
    monkeypatch.setattr(runner, "get_langfuse", lambda: lf)

    ok, bad = make_listing(id=1), make_listing(id=2)
    db = FakeDB()
    summary = asyncio.run(runner.run_scoring([ok, bad], db, client=fake, retriever=no_docs_retriever))

    assert summary.total == 2
    assert summary.scored_ids == [1]
    assert summary.failed[0][0] == 2
    assert ok.score == 8.5 and bad.score is None
    assert ok.score_status == "llm"
    assert bad.score_status == "pending"          # primer fallo: se reintenta en la próxima corrida
    assert ok.score_attempts == 1 and bad.score_attempts == 1
    assert db.commits == 3                         # 2 intentos contados + 1 score guardado
    root_spans = [o for o in lf.log if o.name == "score_listing"]
    assert len(root_spans) == 2 and all(o.ended for o in root_spans)
    assert lf.flushed


# ── Etapa 2: reset_and_rescore y notificación en run_all ──────────────────────

class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def limit(self, n):
        return FakeQuery(self.rows[:n])

    def all(self):
        return list(self.rows)


class FakeQueryDB(FakeDB):
    def __init__(self, rows, **kwargs):
        super().__init__(**kwargs)
        self.rows = rows

    def query(self, model):
        return FakeQuery(self.rows)


def _scored_listing(id_):
    return make_listing(id=id_, score=5.0, score_reasoning="viejo", scored_at=datetime(2026, 1, 1))


def test_reset_dry_run_no_toca_nada(monkeypatch, capsys):
    from backend.agents import reset_and_rescore as rr

    async def must_not_score(*a, **k):
        raise AssertionError("dry-run no debe puntuar")

    monkeypatch.setattr(rr, "run_scoring", must_not_score)
    rows = [_scored_listing(i) for i in range(1, 4)]
    db = FakeQueryDB(rows)
    n = asyncio.run(rr.main(confirm=False, limit=None, db=db))

    assert n == 3
    assert db.commits == 0
    assert all(r.score == 5.0 and r.scored_at is not None for r in rows)
    assert "[dry-run] Se resetearían y puntuarían 3 listings" in capsys.readouterr().out


def test_reset_confirm_con_limit_resetea_y_puntua_solo_n(monkeypatch):
    from backend.agents import reset_and_rescore as rr
    from backend.scoring import rag
    monkeypatch.setattr(rag, "_default_retriever", no_docs_retriever)
    rows = [_scored_listing(i) for i in range(1, 4)]
    fake = FakeClient(tool_input=VALID_RESULT)

    n = asyncio.run(rr.main(confirm=True, limit=2, db=FakeQueryDB(rows), client=fake))

    assert n == 2
    assert len(fake.messages.calls) == 2
    assert [r.score for r in rows] == [8.5, 8.5, 5.0]   # el tercero no se tocó
    assert rows[2].scored_at.year == 2026 and rows[2].scored_at.month == 1


def test_reset_confirm_fallo_deja_scored_at_en_null(monkeypatch):
    from backend.agents import reset_and_rescore as rr
    from backend.scoring import rag
    monkeypatch.setattr(rag, "_default_retriever", no_docs_retriever)
    rows = [_scored_listing(1)]

    asyncio.run(rr.main(confirm=True, limit=None, db=FakeQueryDB(rows),
                        client=FakeClient(raise_exc=RuntimeError("503"))))
    assert rows[0].score is None and rows[0].scored_at is None


def test_reset_args():
    from backend.agents.reset_and_rescore import parse_args
    assert parse_args([]).confirm is False
    assert parse_args(["--confirm", "--limit", "5"]).limit == 5
    with pytest.raises(SystemExit):
        parse_args(["--limit", "0"])


def test_notify_scored_solo_recien_puntuados_con_score_alto(monkeypatch):
    from backend.scrapers import run_scrapers
    sent = []

    async def fake_send(listings):
        sent.extend(x.id for x in listings)

    monkeypatch.setattr(run_scrapers, "send_whatsapp_alerts", fake_send)
    alto = make_listing(id=1, score=8.0)
    bajo = make_listing(id=2, score=6.0)
    ya_notificado = make_listing(id=3, score=9.0, notified_at=datetime(2026, 1, 1))
    fallido = make_listing(id=4, score=9.0)          # score viejo, no está en scored_ids
    db = FakeDB()

    asyncio.run(run_scrapers.notify_scored([alto, bajo, ya_notificado, fallido], [1, 2, 3], db))

    assert sent == [1]
    assert alto.notified_at is not None
    assert db.commits == 1


# ── Prompt caching ─────────────────────────────────────────────────────────────

def test_request_lleva_cache_control_en_tool_y_system():
    from backend.scoring.client import request_score
    fake = FakeClient(tool_input=VALID_RESULT)
    asyncio.run(request_score("PISO X", client=fake))
    call = fake.messages.calls[0]

    assert call["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert call["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert call["system"][-1]["type"] == "text"
    # El contexto del piso va al final y sin cache_control.
    assert call["messages"] == [{"role": "user", "content": "PISO X"}]


def test_prefijo_cacheado_identico_entre_listings():
    import json
    from backend.scoring.client import build_request_params
    from backend.scoring.prompt import build_user_message

    a = build_request_params(build_user_message(make_listing(id=1, title="Piso A", price=150000.0)))
    b = build_request_params(build_user_message(make_listing(id=2, title="Piso B", price=390000.0,
                                                             neighborhood="Vallecas")))

    def prefix(p):
        return json.dumps({k: p[k] for k in ("model", "tools", "system", "tool_choice")}, ensure_ascii=False)

    assert prefix(a) == prefix(b)
    assert a["messages"] != b["messages"]
    assert "Piso A" not in prefix(a) and "150,000" not in prefix(a)


def test_score_tool_original_no_se_modifica():
    from backend.scoring.client import CACHED_TOOLS
    from backend.scoring.prompt import SCORE_TOOL
    assert "cache_control" not in SCORE_TOOL
    assert {k: v for k, v in CACHED_TOOLS[0].items() if k != "cache_control"} == SCORE_TOOL


def test_usage_de_cache_llega_a_langfuse():
    usage = SimpleNamespace(input_tokens=420, output_tokens=180,
                            cache_creation_input_tokens=0, cache_read_input_tokens=1350)
    lf = FakeLangfuse()
    _run_graph(FakeClient(tool_input=VALID_RESULT, usage=usage), lf_span=lf)
    gen = next(o for o in lf.log if o.name == "llm_score")
    assert gen.updates[0]["usage_details"] == {
        "input": 420, "output": 180,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1350,
    }


def test_usage_sin_campos_de_cache_no_manda_none():
    from backend.scoring.client import ScoreResponse
    assert ScoreResponse(raw={}, input_tokens=10, output_tokens=5).usage_details() == {"input": 10, "output": 5}
