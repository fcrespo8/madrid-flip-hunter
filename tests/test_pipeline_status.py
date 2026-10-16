"""Tests offline de la parte A: score_status, QA que marca, pre_score, intentos, reset, orden de run_all."""
import asyncio
import io
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from backend.scrapers.base_scraper import ScrapeResult
from tests.test_scoring import (  # noqa: F401  (_offline es un fixture autouse)
    VALID_RESULT,
    FakeClient,
    FakeDB,
    FakeQuery,
    _offline,
    make_listing,
    no_docs_retriever,
)

MIGRATION = "b7e2d9a4c1f0"
PREV_MIGRATION = "f3a1b2c4d5e6"


def _sql(query) -> str:
    return str(query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ── Modelo y migración ─────────────────────────────────────────────────────────

def _render_migration(direction: str) -> str:
    from alembic import command
    from alembic.config import Config

    from unittest.mock import patch
    buf = io.StringIO()
    cfg = Config("alembic.ini", stdout=buf, output_buffer=buf)
    rng = f"{PREV_MIGRATION}:{MIGRATION}" if direction == "upgrade" else f"{MIGRATION}:{PREV_MIGRATION}"
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql://x@localhost/render_only"}):   # dialecto Postgres siempre
        getattr(command, direction)(cfg, rng, sql=True)
    return buf.getvalue()


def test_migracion_es_la_unica_cabeza():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    (head,) = script.get_heads()                       # una sola cabeza
    assert MIGRATION in {r.revision for r in script.walk_revisions()}   # y esta migración sigue en la cadena


def test_migracion_upgrade_sql():
    sql = _render_migration("upgrade")
    for col in ("qa_rejected BOOLEAN DEFAULT false NOT NULL", "qa_reason TEXT",
                "score_status VARCHAR(20) DEFAULT 'pending' NOT NULL", "score_status_reason TEXT",
                "score_attempts INTEGER DEFAULT 0 NOT NULL"):
        assert f"ADD COLUMN {col}" in sql
    # Backfill: primero 'auto' (por el texto del pre-score), después el resto con score → 'llm'.
    assert sql.index("SET score_status = 'auto'") < sql.index("SET score_status = 'llm'")
    assert "CHECK (score_status IN ('pending', 'auto', 'llm', 'unscorable', 'failed'))" in sql
    assert "CREATE INDEX ix_listings_pending ON listings (id) WHERE score_status = 'pending'" in sql


def test_migracion_downgrade_sql():
    sql = _render_migration("downgrade")
    assert "DROP INDEX ix_listings_pending" in sql
    assert "DROP CONSTRAINT ck_listings_score_status" in sql
    for col in ("qa_rejected", "qa_reason", "score_status", "score_status_reason", "score_attempts"):
        assert f"DROP COLUMN {col}" in sql


def test_modelo_coincide_con_la_migracion():
    from backend.models.listing import SCORE_STATUSES, Listing
    table = Listing.__table__
    check = next(c for c in table.constraints if c.name == "ck_listings_score_status")
    assert all(f"'{s}'" in str(check.sqltext) for s in SCORE_STATUSES)
    index = next(i for i in table.indexes if i.name == "ix_listings_pending")
    assert str(index.dialect_options["postgresql"]["where"]) == \
        "score_status = 'pending' AND NOT qa_rejected AND is_active"
    for col in ("qa_rejected", "qa_reason", "score_status", "score_status_reason", "score_attempts"):
        assert col in table.c


def test_consulta_de_pendientes():
    from backend.models.repository import pending_listings_query
    sql = _sql(pending_listings_query(Session()))
    assert "listings.score_status = 'pending'" in sql
    assert "listings.qa_rejected IS false" in sql
    assert "listings.is_active IS true" in sql
    assert "ORDER BY listings.id" in sql


# ── QA ─────────────────────────────────────────────────────────────────────────

class NoDeleteDB(FakeDB):
    def delete(self, obj):
        raise AssertionError("QA no debe borrar")


def test_qa_marca_en_vez_de_borrar(monkeypatch):
    from backend.agents import qa_agent
    alquiler = make_listing(id=1, title="Piso en alquiler en Chamberí")
    barato = make_listing(id=2, price=10_000.0)
    bueno = make_listing(id=3)
    monkeypatch.setattr(qa_agent, "pending_listings_query", lambda db: FakeQuery([alquiler, barato, bueno]))
    db = NoDeleteDB()

    results = qa_agent.QAAgent().run(db)

    assert results["rejected_ids"] == [1, 2]
    assert alquiler.qa_rejected is True and alquiler.qa_reason == "es alquiler, no venta"
    assert barato.qa_rejected is True and "precio muy bajo" in barato.qa_reason
    assert bueno.qa_rejected is False and bueno.qa_reason is None
    assert db.commits == 1


def test_qa_junta_varios_motivos(monkeypatch):
    from backend.agents import qa_agent
    raro = make_listing(id=1, title="Local comercial", size_m2=5.0)
    monkeypatch.setattr(qa_agent, "pending_listings_query", lambda db: FakeQuery([raro]))
    qa_agent.QAAgent().run(NoDeleteDB())
    assert raro.qa_reason == "tamaño muy pequeño: 5.0m²; precio/m² anómalo: 40000€/m²; tipo no residencial"


# ── pre_score ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("overrides, reason", [
    (dict(price=None), "no_price"),
    (dict(price=None, size_m2=None), "no_price"),
    (dict(size_m2=None), "no_size"),
    (dict(neighborhood="Atlantis", district="Narnia"), "no_market_price"),
    (dict(), None),
])
def test_motivo_no_puntuable(overrides, reason):
    from backend.agents.pre_scorer import unscorable_reason
    assert unscorable_reason(make_listing(**overrides)) == reason


def test_apply_pre_scores_clasifica_y_marca():
    from backend.agents.market_prices import get_market_price
    from backend.agents.pre_scorer import AUTO_REASONING, apply_pre_scores

    m2 = 50.0
    market = get_market_price("Lavapiés", "Centro")
    candidato = make_listing(id=1, price=market * m2 * 0.65)     # -35 % → pre-score 9
    auto = make_listing(id=2, price=market * m2)                  # a precio de mercado → 1
    sin_m2 = make_listing(id=3, size_m2=None)
    db = FakeDB()

    result = apply_pre_scores(db, [candidato, auto, sin_m2])

    assert [x.id for x in result.candidates] == [1]
    assert candidato.score is None and candidato.score_status == "pending"
    assert result.auto_ids == [2]
    assert auto.score == 1.0 and auto.score_status == "auto" and auto.score_reasoning == AUTO_REASONING
    assert result.unscorable_ids == [3]
    assert sin_m2.score is None and sin_m2.score_status == "unscorable"
    assert sin_m2.score_status_reason == "no_size"
    assert db.commits == 1


def test_los_candidatos_salen_por_pre_score_descendente_y_a_igual_pre_score_por_id():
    from backend.agents.market_prices import get_market_price
    from backend.agents.pre_scorer import apply_pre_scores, pre_score

    base = get_market_price("Lavapiés", "Centro") * 50.0
    # (id, factor de precio) → pre-score 7 (-22 %), 9 (-35 %), 8 (-28 %), 9, 7
    rows = [(5, 0.78), (9, 0.65), (3, 0.72), (2, 0.65), (1, 0.78)]
    listings = [make_listing(id=i, price=base * f) for i, f in rows]
    assert [pre_score(x) for x in listings] == [7.0, 9.0, 8.0, 9.0, 7.0]

    result = apply_pre_scores(FakeDB(), listings)
    assert [x.id for x in result.candidates] == [2, 9, 3, 1, 5]        # 9: ids 2,9 · 8: id 3 · 7: ids 1,5

    result = apply_pre_scores(FakeDB(), [make_listing(id=i, price=base * f) for i, f in reversed(rows)])
    assert [x.id for x in result.candidates] == [2, 9, 3, 1, 5]        # no depende del orden de entrada


def test_candidate_order_prioriza_pre_score_luego_descuento_luego_id():
    from backend.agents.pre_scorer import candidate_order
    rows = [(7.0, 29.9, 1), (9.0, 31.0, 100), (9.0, 45.0, 200), (9.0, 45.0, 150), (8.0, 28.0, 5)]
    assert sorted(rows, key=lambda t: candidate_order(*t)) == [
        (9.0, 45.0, 150), (9.0, 45.0, 200), (9.0, 31.0, 100), (8.0, 28.0, 5), (7.0, 29.9, 1)]


def test_a_igual_pre_score_sale_primero_el_de_mayor_descuento_aunque_tenga_id_mayor():
    from backend.agents.market_prices import get_market_price
    from backend.agents.pre_scorer import apply_pre_scores, discount_pct

    base = get_market_price("Lavapiés", "Centro") * 50.0
    # Los tres son pre-score 9; el descuento es 31 %, 50 % y 40 %. Los ids van al revés.
    listings = [make_listing(id=1, price=base * 0.69), make_listing(id=2, price=base * 0.50),
                make_listing(id=3, price=base * 0.60)]
    assert [round(discount_pct(x)) for x in listings] == [31, 50, 40]
    result = apply_pre_scores(FakeDB(), listings)
    assert [x.id for x in result.candidates] == [2, 3, 1]


def test_el_descuento_no_pasa_por_encima_del_pre_score():
    """Un 7 con 29,9 % de descuento no se adelanta a un 9 con 30 %: primero manda el pre-score."""
    from backend.agents.market_prices import get_market_price
    from backend.agents.pre_scorer import apply_pre_scores, pre_score

    base = get_market_price("Lavapiés", "Centro") * 50.0
    nueve, ocho = make_listing(id=9, price=base * 0.69), make_listing(id=1, price=base * 0.7499)
    assert (pre_score(nueve), pre_score(ocho)) == (9.0, 8.0)
    assert [x.id for x in apply_pre_scores(FakeDB(), [ocho, nueve]).candidates] == [9, 1]


@pytest.mark.parametrize("ppm2, score", [
    (600, 9.0), (700, 9.0), (701, 8.0), (750, 8.0), (751, 7.0), (800, 7.0),
    (801, 5.0), (850, 5.0), (851, 3.0), (900, 3.0), (901, 1.0), (1000, 1.0), (1200, 1.0),
])
def test_umbrales_exactos_con_mercado_de_1000(monkeypatch, ppm2, score):
    """Con mercado = 1.000 €/m² los porcentajes son exactos: 30 % → 9, 25 % → 8, 20 % → 7, 15 % → 5, 10 % → 3."""
    from backend.agents import pre_scorer
    monkeypatch.setattr(pre_scorer, "get_market_price", lambda *a: 1000.0)
    assert pre_scorer.pre_score(make_listing(size_m2=100.0, price=ppm2 * 100.0)) == score


def test_pre_score_conserva_exactamente_los_umbrales():
    """Misma regla que antes del refactor a discount_pct: umbrales sobre (€/m² − mercado) / mercado."""
    from backend.agents.market_prices import get_market_price
    from backend.agents.pre_scorer import pre_score

    def original(listing):
        market = get_market_price(listing.neighborhood, listing.district)
        vs_pct = (listing.price / listing.size_m2 - market) / market * 100
        for limit, score in ((-30, 9.0), (-25, 8.0), (-20, 7.0), (-15, 5.0), (-10, 3.0)):
            if vs_pct <= limit:
                return score
        return 1.0

    market = get_market_price("Lavapiés", "Centro")
    for size in (37.0, 50.0, 83.0):
        for pct in [x / 10 for x in range(0, 500)] + [10, 15, 20, 25, 30]:
            listing = make_listing(size_m2=size, price=market * size * (1 - pct / 100))
            assert pre_score(listing) == original(listing), (size, pct)


# ── Runner: intentos, llm y failed ─────────────────────────────────────────────

def _score(listing, client):
    from backend.scoring.runner import run_scoring
    db = FakeDB()
    summary = asyncio.run(run_scoring([listing], db, client=client, retriever=no_docs_retriever))
    return summary, db


def test_exito_marca_llm():
    listing = make_listing(score_attempts=1)
    summary, _ = _score(listing, FakeClient(tool_input=VALID_RESULT))
    assert summary.scored_ids == [1]
    assert listing.score_status == "llm" and listing.score_status_reason is None
    assert listing.score_attempts == 2


def test_falla_queda_pending_hasta_el_tercer_intento():
    listing = make_listing()
    for attempt in (1, 2):
        _score(listing, FakeClient(raise_exc=RuntimeError("503")))
        assert listing.score_attempts == attempt
        assert listing.score_status == "pending"

    _score(listing, FakeClient(raise_exc=RuntimeError("503")))
    assert listing.score_attempts == 3
    assert listing.score_status == "failed"
    assert listing.score_status_reason == "LLMCallError: 503"


def test_validacion_invalida_tambien_cuenta_como_intento():
    listing = make_listing(score_attempts=2)
    _score(listing, FakeClient(tool_input={**VALID_RESULT, "score": 99}))
    assert listing.score_status == "failed"
    assert listing.score_status_reason.startswith("ScoreValidationError")
    assert listing.score is None


def test_error_inesperado_cuenta_como_intento():
    listing = make_listing(price=None, score_attempts=2)   # build_user_message falla con price None
    summary, db = _score(listing, FakeClient(tool_input=VALID_RESULT))
    assert summary.failed[0][0] == 1
    assert listing.score_status == "failed" and listing.score_status_reason.startswith("TypeError")
    assert db.rollbacks == 1


def test_intento_se_guarda_antes_de_llamar_a_claude():
    listing = make_listing()
    db = FakeDB()
    seen = {}

    class SpyMessages:
        async def create(self, **kwargs):
            seen["commits"], seen["attempts"] = db.commits, listing.score_attempts
            raise RuntimeError("boom")

    from backend.scoring.runner import run_scoring
    asyncio.run(run_scoring([listing], db, client=SimpleNamespace(messages=SpyMessages()),
                            retriever=no_docs_retriever))
    assert seen == {"commits": 1, "attempts": 1}


# ── reset_and_rescore ──────────────────────────────────────────────────────────

def test_reset_vuelve_a_pending_y_limpia_intentos():
    from backend.agents.reset_and_rescore import reset_scores
    listing = make_listing(score=4.0, score_status="failed", score_status_reason="LLMCallError: 503",
                           score_attempts=3)
    reset_scores(FakeDB(), [listing])
    assert (listing.score, listing.score_status, listing.score_status_reason, listing.score_attempts) == \
        (None, "pending", None, 0)


def test_reset_excluye_rechazados_y_no_puntuables(monkeypatch):
    from sqlalchemy.orm import Query
    from backend.agents import reset_and_rescore as rr
    captured = {}
    monkeypatch.setattr(Query, "all", lambda self: captured.setdefault("sql", _sql(self)) and [])

    rr.select_targets(Session(), limit=5)

    sql = captured["sql"]
    assert "listings.is_active IS true" in sql
    assert "listings.qa_rejected IS false" in sql
    assert "listings.score_status != 'unscorable'" in sql
    assert "LIMIT 5" in sql


# ── run_all: orden de los pasos ────────────────────────────────────────────────

def test_run_all_desactiva_antes_de_seleccionar_pendientes(monkeypatch):
    from backend.scrapers import run_scrapers as rs
    calls = []

    class FakeScraper:
        source_name = "fake"

        async def run(self):
            calls.append("scrape")
            return ScrapeResult.finish([], reached_end=True)

    class Summary:
        scored_ids = []

    class RunAllDB(FakeDB):
        def expire_all(self):
            pass

        def close(self):
            pass

    async def fake_run_scoring(candidates, db):
        calls.append("score")
        return Summary()

    async def fake_notify(candidates, scored_ids, db):
        calls.append("notify")

    for name in ("WallapopScraper", "DonpisoScraper", "RemaxScraper", "RedpisoScraper", "TecnocasaScraper"):
        monkeypatch.setattr(rs, name, FakeScraper)
    monkeypatch.setattr(rs, "SessionLocal", RunAllDB)
    monkeypatch.setattr(rs, "record_scrape_run", lambda *a: None)
    monkeypatch.setattr(rs, "deactivate_stale", lambda sources: calls.append("deactivate_stale"))
    monkeypatch.setattr(rs.QAAgent, "run", lambda self, db: calls.append("qa"))
    monkeypatch.setattr(rs, "enrich_locations", lambda: calls.append("enrich_locations"))
    monkeypatch.setattr(rs, "pending_listings_query", lambda db: calls.append("select_pending") or FakeQuery([]))
    monkeypatch.setattr(rs, "run_scoring", fake_run_scoring)
    monkeypatch.setattr(rs, "notify_scored", fake_notify)

    asyncio.run(rs.run_all())

    assert calls == ["scrape"] * 5 + [
        "deactivate_stale", "qa", "enrich_locations", "select_pending", "score", "notify",
    ]


# ── enrich_sizes: salida de unscorable ────────────────────────────────────────

def test_enrich_size_devuelve_unscorable_no_size_a_pending():
    from backend.agents.enrich_size import apply_size
    listing = make_listing(size_m2=None, score_status="unscorable", score_status_reason="no_size")
    assert apply_size(listing, 62.0) is True
    assert listing.size_m2 == 62.0
    assert listing.score_status == "pending" and listing.score_status_reason is None


@pytest.mark.parametrize("status, reason", [
    ("unscorable", "no_market_price"),   # el tamaño no era el problema
    ("unscorable", "no_price"),
    ("failed", "LLMCallError: 503"),
    ("pending", None),
])
def test_enrich_size_no_toca_otros_estados(status, reason):
    from backend.agents.enrich_size import apply_size
    listing = make_listing(size_m2=None, score_status=status, score_status_reason=reason)
    assert apply_size(listing, 62.0) is False
    assert listing.size_m2 == 62.0
    assert (listing.score_status, listing.score_status_reason) == (status, reason)
