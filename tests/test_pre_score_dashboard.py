"""Pre-score guardado en la DB (pre_score, discount_pct, market_zone), API ordenada por pre-score y
descuento, y dashboard con ⭐ para los puntuados por Claude."""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import requires_test_db
from tests.test_scoring import FakeDB, _offline, make_listing  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "frontend" / "index.html"


# ── 1. Zona de referencia ──────────────────────────────────────────────────────

@pytest.mark.parametrize("barrio, distrito, expected", [
    ("Lavapiés", "Centro", ("Lavapiés", 6176, "exacto")),
    ("Chamberí", None, ("Chamberí", 8992, "exacto")),
    ("CHAMBERI", None, ("Chamberí", 8992, "normalizado")),
    ("tetuan", None, ("Tetuán", 6034, "normalizado")),
    ("Moncloa - Aravaca", None, ("Moncloa", 6376, "parcial")),
    (None, "Latina", ("Latina", 3916, "exacto")),
    ("Atlantis", "Latina", ("Latina", 3916, "exacto")),       # el barrio no existe: cae al distrito
    ("Atlantis", "Narnia", None),
    (None, None, None),
    ("", "", None),
])
def test_match_market_zone(barrio, distrito, expected):
    from backend.agents.market_prices import match_market_zone
    assert match_market_zone(barrio, distrito) == expected


def test_get_market_price_da_lo_mismo_que_antes_de_agregar_la_zona():
    """get_market_price ahora usa match_market_zone: no puede cambiar ningún precio."""
    from backend.agents import market_prices as mp

    def original(neighborhood, district=None):          # la implementación de antes, copiada tal cual
        for key in [neighborhood, district]:
            if not key:
                continue
            if key in mp.PRECIOS_MADRID:
                return mp.PRECIOS_MADRID[key]
            norm = mp._normalize(key)
            if norm in mp._NORMALIZED:
                return mp._NORMALIZED[norm]
            for k, v in mp._NORMALIZED.items():
                if k in norm or norm in k:
                    return v
        return None

    names = list(mp.PRECIOS_MADRID) + [n.upper() for n in mp.PRECIOS_MADRID] + [mp._normalize(n) for n in mp.PRECIOS_MADRID]
    names += ["Salamanca - La Guindalera", "Calle de Ezequiel Solana", "Moncloa - Aravaca", "Bellas Vistas, Tetuan",
              "Fuencarral-El Pardo - La Paz", "Atlantis", "x", "", None]
    for a in names:
        assert mp.get_market_price(a) == original(a), a
        for b in ("Centro", "Latina", None, "Narnia"):
            assert mp.get_market_price(a, b) == original(a, b), (a, b)


def test_la_zona_devuelta_es_una_clave_de_la_tabla_con_ese_precio():
    from backend.agents import market_prices as mp
    for name in list(mp.PRECIOS_MADRID) + ["Moncloa - Aravaca", "Salamanca - La Guindalera", "chamberi"]:
        zone, price, kind = mp.match_market_zone(name)
        assert zone in mp.PRECIOS_MADRID and mp.PRECIOS_MADRID[zone] == price and kind in ("exacto", "normalizado", "parcial")


# ── 2. El pre-scorer guarda los campos ─────────────────────────────────────────

def _base():
    from backend.agents.market_prices import get_market_price
    return get_market_price("Lavapiés", "Centro") * 50.0


def test_apply_pre_scores_guarda_pre_score_descuento_y_zona():
    from backend.agents.pre_scorer import apply_pre_scores
    base = _base()
    nueve = make_listing(id=1, price=base * 0.65)
    auto = make_listing(id=2, price=base * 0.88)
    ok = make_listing(id=3, price=base)
    apply_pre_scores(FakeDB(), [nueve, auto, ok])

    assert (nueve.pre_score, nueve.market_zone) == (9.0, "Lavapiés")
    assert nueve.discount_pct == pytest.approx(35.0)
    assert (auto.pre_score, auto.score_status) == (3.0, "auto") and auto.discount_pct == pytest.approx(12.0)
    assert ok.pre_score == 1.0 and ok.discount_pct == pytest.approx(0.0, abs=1e-9)
    assert nueve.score is None                       # el pre-score NO es el score de un candidato a Claude


def test_el_descuento_guardado_es_negativo_si_esta_mas_caro_que_el_mercado():
    from backend.agents.pre_scorer import apply_pre_scores
    listing = make_listing(price=_base() * 1.2)
    apply_pre_scores(FakeDB(), [listing])
    assert (listing.pre_score, listing.score_status) == (1.0, "auto")
    assert listing.discount_pct == pytest.approx(-20.0)


@pytest.mark.parametrize("overrides, status_reason, pre, discount, zone", [
    (dict(size_m2=None), "no_size", None, None, "Lavapiés"),             # hay zona, falta el tamaño
    (dict(price=None), "no_price", None, None, "Lavapiés"),
    (dict(neighborhood="Atlantis", district="Narnia"), "no_market_price", None, None, None),
])
def test_un_unscorable_guarda_lo_que_pueda_y_deja_el_resto_en_none(overrides, status_reason, pre, discount, zone):
    from backend.agents.pre_scorer import apply_pre_scores
    listing = make_listing(**overrides)
    apply_pre_scores(FakeDB(), [listing])
    assert listing.score_status_reason == status_reason
    assert (listing.pre_score, listing.discount_pct, listing.market_zone) == (pre, discount, zone)


def test_se_recalcula_en_cada_corrida():
    """Si el listing cambió (o cambió la tabla), el valor viejo se pisa; si ya no se puede calcular, vuelve a None."""
    from backend.agents.pre_scorer import apply_pre_scores
    listing = make_listing(price=_base() * 0.65, pre_score=1.0, discount_pct=0.0, market_zone="Sol")
    apply_pre_scores(FakeDB(), [listing])
    assert (listing.pre_score, listing.market_zone) == (9.0, "Lavapiés")
    listing.size_m2 = None
    apply_pre_scores(FakeDB(), [listing])
    assert (listing.pre_score, listing.discount_pct) == (None, None)


def test_los_campos_guardados_coinciden_con_la_logica_del_pre_scorer():
    from backend.agents.pre_scorer import discount_pct, pre_score, pre_score_fields
    for factor in (0.5, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0, 1.3):
        listing = make_listing(price=_base() * factor)
        fields = pre_score_fields(listing)
        assert (fields.pre_score, fields.discount_pct) == (pre_score(listing), discount_pct(listing))


def test_no_cambia_el_orden_de_los_candidatos():
    from backend.agents.pre_scorer import apply_pre_scores
    base = _base()
    listings = [make_listing(id=1, price=base * 0.78), make_listing(id=2, price=base * 0.5), make_listing(id=3, price=base * 0.65)]
    assert [x.id for x in apply_pre_scores(FakeDB(), listings).candidates] == [2, 3, 1]


# ── 3. Modelo y migración ──────────────────────────────────────────────────────

NEW_COLUMNS = {"pre_score", "discount_pct", "market_zone"}


def test_columnas_del_modelo():
    from backend.models.listing import Listing
    columns = Listing.__table__.columns
    assert NEW_COLUMNS <= set(columns.keys())
    assert all(columns[c].nullable for c in NEW_COLUMNS)
    assert columns["market_zone"].type.length == 100


def _migration():
    path = next((ROOT / "alembic" / "versions").glob("*_add_pre_score_fields.py"))
    return path, ast.parse(path.read_text())


def test_la_migracion_agrega_y_quita_exactamente_esas_columnas():
    _, tree = _migration()
    funcs = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}

    def calls(fn, attr):
        return [n for n in ast.walk(funcs[fn]) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == attr]

    added = {c.args[1].args[0].value for c in calls("upgrade", "add_column")}
    dropped = {c.args[1].value for c in calls("downgrade", "drop_column")}
    assert added == dropped == NEW_COLUMNS
    assert all(c.args[0].value == "listings" for c in calls("upgrade", "add_column") + calls("downgrade", "drop_column"))


def test_la_migracion_cuelga_de_scrape_runs_y_es_la_unica_cabeza():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1
    rev = script.get_revision(heads[0])
    assert rev.down_revision == "c4d8f1a7e3b2" and "pre_score" in rev.doc


@requires_test_db
def test_las_columnas_existen_en_la_db_de_prueba():
    from sqlalchemy import inspect
    from backend.models.database import engine
    assert NEW_COLUMNS <= {c["name"] for c in inspect(engine).get_columns("listings")}


# ── 4. Backfill ────────────────────────────────────────────────────────────────

SRC = "pytest-prescore"


@pytest.fixture
def seeded_listings():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing

    def add(db, key, **kw):
        data = dict(source=SRC, external_id=key, url=key, title=key, price=_base() * 0.65, size_m2=50.0,
                    neighborhood="Lavapiés", district="Centro", is_active=True, qa_rejected=False,
                    score_status="llm", score=6.5, score_reasoning="Claude dijo", scored_at=None)
        data.update(kw)
        db.add(Listing(**data))

    db = SessionLocal()
    add(db, "nuevo")                                                              # sin campos → hay que rellenar
    add(db, "auto", score_status="auto", score=3.0, price=_base() * 0.88)
    add(db, "sin-tamano", size_m2=None, score_status="unscorable", score=None, score_reasoning=None)
    add(db, "ya-calculado", pre_score=9.0, discount_pct=35.0, market_zone="Lavapiés")
    add(db, "desactualizado", pre_score=1.0, discount_pct=0.0, market_zone="Sol")   # solo lo toca --all
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(Listing).filter(Listing.source == SRC).delete()
    db.commit()
    db.close()


def _snapshot():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()
    try:
        return {x.external_id: x for x in db.query(Listing).filter(Listing.source == SRC)}
    finally:
        db.close()


@requires_test_db
def test_backfill_dry_run_no_escribe(seeded_listings, capsys):
    from scripts import backfill_pre_scores as bf
    assert bf.main([]) == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    rows = _snapshot()
    assert rows["nuevo"].pre_score is None and rows["auto"].pre_score is None


@requires_test_db
def test_backfill_confirm_rellena_sin_tocar_score_ni_estado(seeded_listings):
    from scripts import backfill_pre_scores as bf
    before = _snapshot()
    assert bf.main(["--confirm"]) == 0
    rows = _snapshot()

    assert (rows["nuevo"].pre_score, rows["nuevo"].market_zone) == (9.0, "Lavapiés")
    assert rows["nuevo"].discount_pct == pytest.approx(35.0)
    assert rows["auto"].pre_score == 3.0
    assert (rows["sin-tamano"].pre_score, rows["sin-tamano"].discount_pct, rows["sin-tamano"].market_zone) == (None, None, "Lavapiés")
    assert rows["ya-calculado"].pre_score == 9.0
    assert rows["desactualizado"].pre_score == 1.0                       # sin --all no se toca lo que ya tiene valores
    for key, row in rows.items():                                         # lo que NO es del pre-scorer, intacto
        assert (row.score, row.score_status, row.score_reasoning, row.score_status_reason, row.is_active) == \
               (before[key].score, before[key].score_status, before[key].score_reasoning,
                before[key].score_status_reason, before[key].is_active)


@requires_test_db
def test_backfill_all_recalcula_tambien_los_que_ya_tenian_valores(seeded_listings):
    from scripts import backfill_pre_scores as bf
    assert bf.main(["--confirm", "--all"]) == 0
    rows = _snapshot()
    assert (rows["desactualizado"].pre_score, rows["desactualizado"].market_zone) == (9.0, "Lavapiés")


@requires_test_db
def test_backfill_es_idempotente(seeded_listings, capsys):
    from scripts import backfill_pre_scores as bf
    bf.main(["--confirm"])
    capsys.readouterr()
    assert bf.main(["--confirm"]) == 0
    assert "a actualizar: 0" in capsys.readouterr().out


# ── 5. API ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(seeded_api):
    from fastapi.testclient import TestClient
    from backend.api import main
    from backend.auth.dependencies import get_current_user
    main.app.dependency_overrides[get_current_user] = lambda: {"sub": "test"}
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def seeded_api():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()

    def add(key, **kw):
        data = dict(source=SRC, external_id=key, url=f"https://x/{key}", title=key, price=300000.0, size_m2=60.0,
                    neighborhood="Lavapiés", district="Centro", is_active=True, qa_rejected=False,
                    score_status="auto")
        data.update(kw)
        db.add(Listing(**data))

    add("a-nueve-llm", pre_score=9.0, discount_pct=31.0, market_zone="Lavapiés", score_status="llm", score=6.5)
    add("b-nueve-mas-desc", pre_score=9.0, discount_pct=45.0, market_zone="Centro", score_status="pending")
    add("c-ocho-llm", pre_score=8.0, discount_pct=27.0, market_zone="Latina", score_status="llm", score=8.0)
    add("d-siete-auto-no", pre_score=7.0, discount_pct=21.0, market_zone="Sol", score_status="pending")
    add("e-tres-auto", pre_score=3.0, discount_pct=11.0, market_zone="Lavapiés", score_status="auto", score=3.0)
    add("f-sin-pre-con-claude", pre_score=None, score_status="llm", score=7.0)                  # anterior a la migración
    add("g-nueve-igual-desc", pre_score=9.0, discount_pct=31.0, market_zone="Lavapiés", score_status="pending")
    # Inconsistente a propósito (en datos reales el descuento crece con el pre-score): fija qué manda en el orden.
    add("h-ocho-desc-alto", pre_score=8.0, discount_pct=29.0, market_zone="Latina", score_status="pending")
    add("i-score7-pre9", pre_score=9.0, discount_pct=30.0, market_zone="Centro", score_status="llm", score=7.0)
    add("j-ocho-desc-40", pre_score=8.0, discount_pct=40.0, market_zone="Latina", score_status="pending")
    add("x-inactivo", pre_score=9.0, discount_pct=50.0, is_active=False)
    add("x-qa", pre_score=9.0, discount_pct=60.0, qa_rejected=True)
    add("x-nada", pre_score=None, score=None, score_status="unscorable")
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.query(Listing).filter(Listing.source == SRC).delete()
    db.commit()
    db.close()


def _mine(response):
    assert response.status_code == 200
    return [r for r in response.json() if r["source"] == SRC]


@requires_test_db
def test_api_ordena_por_pre_score_y_descuento(client):
    rows = _mine(client.get("/api/listings"))
    assert [r["title"] for r in rows] == ["b-nueve-mas-desc", "a-nueve-llm", "g-nueve-igual-desc", "i-score7-pre9",
                                          "j-ocho-desc-40", "h-ocho-desc-alto", "c-ocho-llm",   # pre 8: manda el pre-score; entre iguales, el descuento
                                          "d-siete-auto-no", "e-tres-auto", "f-sin-pre-con-claude"]


@requires_test_db
def test_api_excluye_inactivos_rechazados_y_sin_nada(client):
    titles = {r["title"] for r in _mine(client.get("/api/listings"))}
    assert not titles & {"x-inactivo", "x-qa", "x-nada"}


@requires_test_db
def test_api_expone_los_campos_del_pre_scorer(client):
    row = next(r for r in _mine(client.get("/api/listings")) if r["title"] == "b-nueve-mas-desc")
    assert (row["pre_score"], row["discount_pct"], row["market_zone"]) == (9.0, 45.0, "Centro")
    assert (row["claude_score"], row["has_claude"], row["score_status"]) == (None, False, "pending")


@requires_test_db
def test_api_claude_score_solo_si_lo_puntuo_claude(client):
    rows = {r["title"]: r for r in _mine(client.get("/api/listings"))}
    assert (rows["a-nueve-llm"]["claude_score"], rows["a-nueve-llm"]["has_claude"]) == (6.5, True)
    assert (rows["c-ocho-llm"]["claude_score"], rows["c-ocho-llm"]["has_claude"]) == (8.0, True)
    # 'auto': score es la nota del pre-scorer, no de Claude → sin ⭐
    assert (rows["e-tres-auto"]["score"], rows["e-tres-auto"]["claude_score"], rows["e-tres-auto"]["has_claude"]) == (3.0, None, False)
    assert rows["d-siete-auto-no"]["has_claude"] is False


@requires_test_db
def test_api_sort_score_ordena_por_la_nota_final(client):
    titles = [r["title"] for r in _mine(client.get("/api/listings?sort=score"))]
    # 8.0 · 7.0 (a igual nota, el de mayor pre-score primero) · 7.0 · 6.5 · 3.0 · sin score al final
    assert titles[:6] == ["c-ocho-llm", "i-score7-pre9", "f-sin-pre-con-claude", "a-nueve-llm", "e-tres-auto", "b-nueve-mas-desc"]


@requires_test_db
def test_api_sort_invalido_es_422(client):
    assert client.get("/api/listings?sort=precio").status_code == 422


@requires_test_db
def test_api_redondea_el_descuento_a_un_decimal(client):
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    db = SessionLocal()
    db.query(Listing).filter_by(source=SRC, external_id="e-tres-auto").update({"discount_pct": 11.2345})
    db.commit()
    db.close()
    row = next(r for r in _mine(client.get("/api/listings")) if r["title"] == "e-tres-auto")
    assert row["discount_pct"] == 11.2


def test_listing_to_dict_sin_campos_nuevos_no_rompe():
    from backend.api.main import listing_to_dict
    row = listing_to_dict(make_listing(score=None, score_status="pending"))
    assert (row["pre_score"], row["discount_pct"], row["market_zone"], row["claude_score"], row["has_claude"]) == \
           (None, None, None, None, False)


def test_listing_to_dict_conserva_los_campos_de_antes():
    from backend.api.main import listing_to_dict
    row = listing_to_dict(make_listing(score=7.0, score_status="llm"))
    for key in ("id", "title", "price", "size_m2", "rooms", "neighborhood", "district", "lat", "lon", "score",
                "score_reasoning", "score_green_flags", "score_red_flags", "url", "source", "market_price_m2",
                "vs_market_pct", "last_seen_at", "created_at", "scored_at"):
        assert key in row


# ── 6. Dashboard (JS probado con node) ─────────────────────────────────────────

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="hace falta node")


def _script():
    html = INDEX_HTML.read_text()
    return html[html.rindex("<script>") + len("<script>"):html.rindex("</script>")]


def _snippet(start, end):
    script = _script()
    return script[script.index(start):script.index(end)]


def _node(code):
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_el_script_del_dashboard_es_javascript_valido(tmp_path):
    path = tmp_path / "dashboard.js"
    path.write_text(_script())
    out = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr


@needs_node
def test_estrella_y_nota_solo_si_hay_nota_de_claude():
    helpers = _snippet("const hasClaude", "function scoreColor")
    rows = _node(helpers + """
      const cases = [
        {has_claude: true, claude_score: 7.5, discount_pct: 27.4},
        {has_claude: false, claude_score: null, discount_pct: 27.4},
        {has_claude: true, claude_score: null},
        {discount_pct: -6.1},
        {discount_pct: 0},
        {discount_pct: null},
      ];
      console.log(JSON.stringify(cases.map(l => ({claude: claudeHtml(l), discount: fmtDiscount(l), has: hasClaude(l)}))));
    """)
    assert "⭐ 7.5" in rows[0]["claude"] and rows[0]["has"] is True and rows[0]["discount"] == "−27.4%"
    for i in (1, 2):
        assert "⭐" not in rows[i]["claude"] and rows[i]["has"] is False
    assert [r["discount"] for r in rows[3:]] == ["+6.1%", "+0.0%", "—"]


@needs_node
def test_orden_del_dashboard_pre_score_luego_descuento_sin_dato_al_final():
    fn = _snippet("function byPreThenDiscount", "function applyFilters")
    ids = _node(fn + """
      const L = [
        {id: 'sin-pre', pre_score: null, discount_pct: 80},
        {id: '7', pre_score: 7, discount_pct: 29},
        {id: '9-31', pre_score: 9, discount_pct: 31},
        {id: '9-45', pre_score: 9, discount_pct: 45},
        {id: '9-sin-desc', pre_score: 9, discount_pct: null},
        {id: '8', pre_score: 8, discount_pct: 26},
      ];
      console.log(JSON.stringify(L.sort(byPreThenDiscount).map(l => l.id)));
    """)
    assert ids == ["9-45", "9-31", "9-sin-desc", "8", "7", "sin-pre"]


def test_el_dashboard_usa_los_campos_que_devuelve_la_api():
    from backend.api.main import listing_to_dict
    keys = set(listing_to_dict(make_listing()))
    used = set(re.findall(r"\bl\.(pre_score|discount_pct|market_zone|claude_score|has_claude|score_status)\b", _script()))
    assert used == {"pre_score", "discount_pct", "market_zone", "claude_score", "has_claude", "score_status"}
    assert used <= keys


def test_el_dashboard_ordena_por_defecto_por_pre_score_y_ofrece_el_filtro_claude():
    html = INDEX_HTML.read_text()
    sort_select = html[html.index('id="f-sort"'):html.index('id="f-no-size"')]
    assert re.findall(r'<option value="([^"]+)"', sort_select)[0] == "pre-desc"       # la primera opción es la que se aplica al cargar
    for value in ("disc-desc", "claude-desc"):
        assert f'value="{value}"' in sort_select
    assert "⭐ Claude" in html
