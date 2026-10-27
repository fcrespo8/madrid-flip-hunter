"""IPV del INE: parser de Tempus, cargador, market_indices, rollforward_factor y get_reference_price(update_to=...).
Todo offline: el JSON de prueba (tests/data/ine_ipv_madrid_sample.json) es sintético y sigue el formato de Tempus."""
import ast
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import requests
from sqlalchemy.exc import IntegrityError

from backend.market.importer import import_registral
from backend.market.ipv import (
    ALREADY_IMPORTED, IMPORTED, IPVFetchError, IPVParseError, classify_series, fetch_tempus, import_ipv,
    matched_series_names, parse_tempus_ipv, quarter_end,
)
from backend.market.reference import get_reference_price
from backend.market.resolver import ZoneMatch
from backend.market.rollforward import (
    IndexNotAvailable, Period, index_value, latest_quarter, parse_period, rollforward_factor,
)
from backend.market.zones import seed_zones
from tests.conftest import requires_test_db
from tests.test_market_import import MINI, csv_bytes, mini_lines, specs_of

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = ROOT / "tests" / "data" / "ine_ipv_madrid_sample.json"


def sample_payload():
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


def as_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def serie(nombre, data):
    return {"COD": "X", "Nombre": nombre, "Data": data}


def quarter_item(year, quarter, value):
    return {"Fecha": 0, "FK_Periodo": 18 + quarter, "Anyo": year, "Valor": value}


# ── Series de Tempus ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("Madrid, Comunidad de. General. Índice.", "general"),
    ("Madrid, Comunidad de. Vivienda nueva. Índice.", "nueva"),
    ("Madrid, Comunidad de. Vivienda de segunda mano. Índice.", "segunda_mano"),
    ("MADRID, COMUNIDAD DE. Vivienda de Segunda Mano. Índice", "segunda_mano"),
    ("Madrid, Comunidad de. General. Variación anual.", None),
    ("Madrid, Comunidad de. General. Variación trimestral.", None),
    ("Madrid, Comunidad de. Ponderaciones. Vivienda de segunda mano.", None),
    ("Cataluña. General. Índice.", None),
    ("Total Nacional. Vivienda de segunda mano. Índice.", None),
    ("Madrid. General. Índice.", None),                       # sin 'Comunidad de': no es la comunidad autónoma
    ("Madrid, Comunidad de. Índice.", None),                  # sin tipo de vivienda
    ("", None),
])
def test_classify_series(name, expected):
    assert classify_series(name) == expected


def test_quarter_end():
    assert quarter_end(2026, 1) == date(2026, 3, 31)
    assert quarter_end(2026, 2) == date(2026, 6, 30)
    assert quarter_end(2026, 3) == date(2026, 9, 30)
    assert quarter_end(2024, 4) == date(2024, 12, 31)


# ── Parser ─────────────────────────────────────────────────────────────────────

def test_parsea_el_json_de_prueba_y_se_queda_solo_con_los_indices_de_madrid():
    points = parse_tempus_ipv(sample_payload())
    assert len(points) == 30                                          # 3 series × 10 trimestres (2024T1…2026T2)
    assert {p.series for p in points} == {"general", "nueva", "segunda_mano"}
    segunda = [p for p in points if p.series == "segunda_mano"]
    assert [p.period for p in segunda][0] == "2024T1" and [p.period for p in segunda][-1] == "2026T2"
    assert segunda[-1].value == 112.2 and segunda[-1].period_end == date(2026, 6, 30)
    assert sum(p.value for p in segunda if p.period.startswith("2025")) / 4 == pytest.approx(100.0)      # base 2025 = 100


def test_ignora_variaciones_ponderaciones_y_otras_regiones():
    names = matched_series_names(sample_payload())
    assert names == {"general": "Madrid, Comunidad de. General. Índice.",
                     "nueva": "Madrid, Comunidad de. Vivienda nueva. Índice.",
                     "segunda_mano": "Madrid, Comunidad de. Vivienda de segunda mano. Índice."}


def test_un_trimestre_sin_publicar_se_omite():
    payload = [serie("Madrid, Comunidad de. General. Índice.", [quarter_item(2026, 1, 104.0), quarter_item(2026, 2, None)])]
    assert [p.period for p in parse_tempus_ipv(payload)] == ["2026T1"]


@pytest.mark.parametrize("code, quarter", [(19, 1), (20, 2), (21, 3), (22, 4)])
def test_codigos_de_periodo_de_tempus(code, quarter):
    payload = [serie("Madrid, Comunidad de. General. Índice.", [{"Anyo": 2025, "FK_Periodo": code, "Valor": 100.0}])]
    (point,) = parse_tempus_ipv(payload)
    assert point.period == f"2025T{quarter}"


@pytest.mark.parametrize("fecha, quarter", [
    (datetime(2025, 7, 1, tzinfo=timezone.utc), 3),                    # inicio del trimestre, UTC
    (datetime(2025, 6, 30, 22, 0, tzinfo=timezone.utc), 3),            # 1 de julio 00:00 en hora de España (CEST)
    (datetime(2024, 12, 31, 23, 0, tzinfo=timezone.utc), 1),           # 1 de enero 00:00 en hora de España (CET): año 2025, T1
    (datetime(2025, 9, 30, 0, 0, tzinfo=timezone.utc), 3),             # fin del trimestre (medianoche UTC)
    (datetime(2025, 9, 29, 22, 0, tzinfo=timezone.utc), 3),            # 30 de septiembre 00:00 en hora de España
])
def test_si_el_codigo_de_periodo_es_desconocido_se_usa_la_fecha(fecha, quarter):
    payload = [serie("Madrid, Comunidad de. General. Índice.",
                     [{"Anyo": 2025, "FK_Periodo": 99, "Fecha": int(fecha.timestamp() * 1000), "Valor": 100.0}])]
    assert parse_tempus_ipv(payload)[0].period == f"2025T{quarter}"


@pytest.mark.parametrize("payload, message", [
    ({"no": "lista"}, "lista de series"),
    ([], "ninguna serie de índice"),
    ([serie("Cataluña. General. Índice.", [quarter_item(2025, 1, 100.0)])], "ninguna serie de índice"),
    ([serie("Madrid, Comunidad de. General. Índice.", []), serie("Madrid, Comunidad de. General. Índice.", [])], "más de una vez"),
    ([serie("Madrid, Comunidad de. General. Índice.", [quarter_item(2025, 1, 100.0), quarter_item(2025, 1, 101.0)])], "repite trimestres"),
    ([serie("Madrid, Comunidad de. General. Índice.", [quarter_item(2025, 1, 0)])], "valor no válido"),
    ([serie("Madrid, Comunidad de. General. Índice.", [quarter_item(2025, 1, 5000.0)])], "valor no válido"),
    ([serie("Madrid, Comunidad de. General. Índice.", [quarter_item(2025, 1, "100")])], "valor no válido"),
    ([serie("Madrid, Comunidad de. General. Índice.", [quarter_item(2025, 1, True)])], "valor no válido"),
    ([serie("Madrid, Comunidad de. General. Índice.", [{"FK_Periodo": 19, "Valor": 100.0}])], "sin año"),
    ([serie("Madrid, Comunidad de. General. Índice.", [{"Anyo": 2025, "FK_Periodo": 28, "Valor": 100.0}])], "no se pudo determinar el trimestre"),
])
def test_respuestas_mal_formadas(payload, message):
    with pytest.raises(IPVParseError, match=message):
        parse_tempus_ipv(payload)


# ── fetch_tempus (sin red: se inyecta el cliente) ──────────────────────────────

class FakeHttp:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class FakeResponse:
    def __init__(self, status=200, content=b"[]"):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error", response=self)


def test_fetch_tempus_pide_la_tabla_con_los_ultimos_n_datos():
    http = FakeHttp(FakeResponse(content=b'[{"a": 1}]'))
    assert fetch_tempus(25171, 12, timeout=7, http=http) == b'[{"a": 1}]'
    (url, kwargs), = http.calls
    assert url == "https://servicios.ine.es/wstempus/js/es/DATOS_TABLA/25171"
    assert kwargs["params"] == {"nult": 12} and kwargs["timeout"] == 7


@pytest.mark.parametrize("http", [
    FakeHttp(FakeResponse(503, b"<html>503 Service Temporarily Unavailable</html>")),
    FakeHttp(error=requests.Timeout("lento")),
    FakeHttp(error=requests.ConnectionError("sin red")),
    FakeHttp(FakeResponse(200, b"<html>no es json</html>")),
])
def test_fetch_tempus_falla_con_un_error_claro(http):
    with pytest.raises(IPVFetchError):
        fetch_tempus(http=http)


# ── Períodos e índices (puro) ──────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (2025, Period(2025)), ("2025", Period(2025)), ("2026T2", Period(2026, 2)), ("2026-T2", Period(2026, 2)),
    ("2026Q3", Period(2026, 3)), (" 2026 t4 ", Period(2026, 4)),
    (date(2026, 6, 30), Period(2026, 2)), (date(2026, 7, 1), Period(2026, 3)), (datetime(2026, 1, 15, 8, 0), Period(2026, 1)),
])
def test_parse_period(value, expected):
    assert parse_period(value) == expected


@pytest.mark.parametrize("value", [True, "ayer", "2026T5", "2026T0", 1800, 3000, None, 2025.5, "26T2"])
def test_parse_period_rechaza_lo_invalido(value):
    with pytest.raises(ValueError):
        parse_period(value)


def test_labels():
    assert Period(2025).label == "2025" and Period(2026, 2).label == "2026T2"


VALUES = {"2025T1": 98.0, "2025T2": 99.4, "2025T3": 100.8, "2025T4": 101.8, "2026T1": 105.0, "2026T2": 112.2}


def test_index_value_trimestral_y_anual():
    assert index_value(VALUES, Period(2026, 2)) == 112.2
    assert index_value(VALUES, Period(2025)) == pytest.approx((98.0 + 99.4 + 100.8 + 101.8) / 4)


@pytest.mark.parametrize("period, message", [(Period(2026), r"no está completo.*2026T3, 2026T4"), (Period(2026, 3), "2026T3"), (Period(2019), "2019T1")])
def test_index_value_sin_dato(period, message):
    with pytest.raises(IndexNotAvailable, match=message):
        index_value(VALUES, period)


def test_latest_quarter():
    assert latest_quarter(VALUES) == Period(2026, 2)
    assert latest_quarter({"2025T4": 1.0, "2026T1": 2.0, "2024T4": 3.0}) == Period(2026, 1)
    assert latest_quarter({}) is None


# ── Migración y modelo ─────────────────────────────────────────────────────────

def _migration_tree():
    path = next((ROOT / "alembic" / "versions").glob("*_add_market_indices.py"))
    return ast.parse(path.read_text())


def test_modelo_y_migracion_coinciden():
    from backend.models.market import MarketIndex
    columns = set(MarketIndex.__table__.c.keys())
    assert columns == {"id", "source", "geography", "series", "period", "period_end", "value", "import_id"}
    assert not any(c.nullable for c in MarketIndex.__table__.c)
    created = {}
    for node in ast.walk(_migration_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_table":
            created[node.args[0].value] = {c.args[0].value for c in node.args[1:]
                                           if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "Column"}
    assert created == {"market_indices": columns}


def test_la_migracion_es_la_cabeza_y_se_deshace():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert script.get_revision("b9c4e7a1d3f8").down_revision == "a8b3d6f1e2c7"
    down = next(f for f in _migration_tree().body if isinstance(f, ast.FunctionDef) and f.name == "downgrade")
    assert [n.args[0].value for n in ast.walk(down) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "drop_table"] == ["market_indices"]


# ── Contra la base de prueba ───────────────────────────────────────────────────

@pytest.fixture
def db():
    from backend.models.database import SessionLocal
    from backend.models.market import MarketImport, MarketIndex, MarketPrice
    from backend.models.zone import Zone, ZoneAlias

    def wipe(session):
        session.query(MarketIndex).delete()
        session.query(MarketPrice).delete()
        session.query(MarketImport).delete()
        session.query(ZoneAlias).delete()
        session.query(Zone).filter(Zone.parent_id.is_not(None)).delete()
        session.query(Zone).delete()
        session.commit()

    session = SessionLocal()
    wipe(session)
    yield session
    session.rollback()
    wipe(session)
    session.close()


def counts(db):
    from backend.models.market import MarketImport, MarketIndex
    return db.query(MarketImport).count(), db.query(MarketIndex).count()


SAMPLE = as_bytes(sample_payload()) if SAMPLE_PATH.exists() else b"[]"


@requires_test_db
def test_la_primera_carga_inserta_los_puntos_y_registra_la_importacion(db):
    from backend.models.market import MarketImport, MarketIndex
    result = import_ipv(db, SAMPLE, "muestra.json")
    db.commit()
    assert (result.status, result.points_read, result.inserted, result.unchanged, result.revised) == (IMPORTED, 30, 30, 0, 0)
    assert (result.period_from, result.period_to) == ("2024T1", "2026T2")
    assert counts(db) == (1, 30)
    record = db.query(MarketImport).one()
    assert (record.source, record.file_name, record.period_label, record.row_count, record.skipped_count) == ("ine_ipv", "muestra.json", "2024T1-2026T2", 30, 0)
    assert "Comunidad de Madrid" in record.notes and "segunda_mano" in record.notes
    row = db.query(MarketIndex).filter_by(series="segunda_mano", period="2026T2").one()
    assert (row.value, row.period_end, row.geography, row.source, row.import_id) == (112.2, date(2026, 6, 30), "Comunidad de Madrid", "ine_ipv", record.id)


@requires_test_db
def test_el_mismo_contenido_dos_veces_no_hace_nada(db):
    first = import_ipv(db, SAMPLE, "muestra.json")
    db.commit()
    again = import_ipv(db, SAMPLE, "otro_nombre.json")
    assert (again.status, again.import_id, again.inserted, again.changed) == (ALREADY_IMPORTED, first.import_id, 0, False)
    assert counts(db) == (1, 30)


@requires_test_db
def test_una_respuesta_nueva_solo_agrega_los_puntos_nuevos_o_revisados(db):
    import_ipv(db, SAMPLE, "v1.json")
    payload = sample_payload()
    segunda = next(s for s in payload if s["Nombre"].endswith("segunda mano. Índice."))
    segunda["Data"][-1]["Valor"] = 113.0                                              # el INE revisó 2026T2
    segunda["Data"].append(quarter_item(2026, 3, 115.0) | {"Fecha": 0})               # y publicó 2026T3
    result = import_ipv(db, as_bytes(payload), "v2.json")
    db.commit()
    assert (result.status, result.points_read, result.inserted, result.unchanged, result.revised) == (IMPORTED, 31, 2, 29, 1)
    assert counts(db) == (2, 32)
    # la versión vieja sigue ahí y la nueva es la que vale
    from backend.models.market import MarketIndex
    versions = db.query(MarketIndex).filter_by(series="segunda_mano", period="2026T2").order_by(MarketIndex.import_id).all()
    assert [v.value for v in versions] == [112.2, 113.0]
    assert rollforward_factor(db, 2025, "2026T2").index_to == 113.0
    assert rollforward_factor(db, 2025).hasta == "2026T3"


@requires_test_db
def test_una_respuesta_sin_cambios_de_valores_pero_distinta_se_registra_sin_filas(db):
    import_ipv(db, SAMPLE, "v1.json")
    payload = sample_payload()
    payload.reverse()                                                                 # mismo contenido, otro orden → otro hash
    result = import_ipv(db, as_bytes(payload), "v2.json")
    assert (result.status, result.inserted, result.unchanged) == (IMPORTED, 0, 30)
    assert counts(db) == (2, 30)


@requires_test_db
@pytest.mark.parametrize("content, exception", [(b"no es json", IPVParseError), (b"[]", IPVParseError), (b'{"a": 1}', IPVParseError)])
def test_un_contenido_invalido_no_escribe_nada(db, content, exception):
    with pytest.raises(exception):
        import_ipv(db, content, "malo.json")
    assert counts(db) == (0, 0)


@requires_test_db
def test_restricciones_de_la_base(db):
    from backend.models.market import MarketIndex
    result = import_ipv(db, SAMPLE, "muestra.json")
    db.commit()

    def fails(**overrides):
        data = dict(source="ine_ipv", geography="Comunidad de Madrid", series="general", period="2030T1",
                    period_end=date(2030, 3, 31), value=100.0, import_id=result.import_id)
        data.update(overrides)
        db.add(MarketIndex(**data))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    fails(series="mixta")
    fails(value=0.0)
    fails(value=-3.0)
    fails(period="2025T1", period_end=date(2025, 3, 31), series="general")            # (punto, carga) repetido
    assert counts(db) == (1, 30)


# ── rollforward_factor ─────────────────────────────────────────────────────────

@requires_test_db
def test_factor_del_ano_registral_a_hoy(db):
    import_ipv(db, SAMPLE, "muestra.json")
    rf = rollforward_factor(db, 2025)
    assert (rf.desde, rf.hasta, rf.series, rf.geography) == ("2025", "2026T2", "segunda_mano", "Comunidad de Madrid")
    assert rf.index_from == pytest.approx(100.0) and rf.index_to == 112.2
    assert rf.factor == pytest.approx(1.122) and float(rf) == rf.factor
    assert rf.apply(6954.58) == pytest.approx(6954.58 * 1.122)


@requires_test_db
def test_factor_entre_trimestres_fechas_y_series(db):
    import_ipv(db, SAMPLE, "muestra.json")
    assert rollforward_factor(db, "2025T2", "2026T2").factor == pytest.approx(112.2 / 99.4)
    assert rollforward_factor(db, date(2025, 5, 3), date(2026, 6, 30)).factor == pytest.approx(112.2 / 99.4)
    assert rollforward_factor(db, 2024, 2025).factor == pytest.approx(100.0 / ((88.4 + 91.0 + 93.8 + 96.6) / 4))
    assert rollforward_factor(db, 2025, series="general").factor == pytest.approx(111.0 / 100.0)
    assert rollforward_factor(db, 2025, series="nueva").index_to == 106.5
    assert rollforward_factor(db, "2026T2", "2025T2").factor < 1                       # hacia atrás también
    assert rollforward_factor(db, 2025, 2025).factor == pytest.approx(1.0)


@requires_test_db
@pytest.mark.parametrize("desde, hasta, message", [
    (2026, None, r"2026 no está completo.*2026T3"),                 # el año de origen no cerró
    (2019, None, "2019T1"),
    (2025, "2026T4", "2026T4"),                                     # no se extrapola más allá del último dato
    (2025, 2026, "2026 no está completo"),
])
def test_factor_sin_indice_disponible(db, desde, hasta, message):
    import_ipv(db, SAMPLE, "muestra.json")
    with pytest.raises(IndexNotAvailable, match=message):
        rollforward_factor(db, desde, hasta)


@requires_test_db
def test_factor_sin_nada_cargado_o_con_argumentos_invalidos(db):
    with pytest.raises(IndexNotAvailable, match="no hay índices segunda_mano"):
        rollforward_factor(db, 2025)
    import_ipv(db, SAMPLE, "muestra.json")
    with pytest.raises(ValueError, match="serie no válida"):
        rollforward_factor(db, 2025, series="lujo")
    with pytest.raises(IndexNotAvailable, match="no hay índices"):
        rollforward_factor(db, 2025, geography="Cataluña")
    with pytest.raises(ValueError, match="período no válido"):
        rollforward_factor(db, "ayer")


# ── get_reference_price(update_to=...) ─────────────────────────────────────────

def zone_id(db, level, code):
    from backend.models.zone import Zone
    return db.query(Zone).filter_by(level=level, code=code).one().id


@pytest.fixture
def prices(db):
    seed_zones(db, specs_of(MINI))
    import_registral(db, MINI, "mini.csv")
    db.commit()
    return db


@requires_test_db
def test_sin_update_to_nada_cambia(prices):
    ref = get_reference_price(prices, zone_id(prices, "barrio", "011"))
    assert (ref.price_m2, ref.updated_price_m2, ref.rollforward, ref.rollforward_error) == (6954.58, None, None, None)


@requires_test_db
def test_devuelve_original_actualizado_y_factor(prices):
    import_ipv(prices, SAMPLE, "muestra.json")
    ref = get_reference_price(prices, zone_id(prices, "barrio", "011"), update_to="latest")
    assert ref.price_m2 == 6954.58 and ref.level == "barrio"                              # el original queda intacto
    assert ref.updated_price_m2 == pytest.approx(6954.58 * 1.122)
    assert ref.rollforward.factor == pytest.approx(1.122)
    assert (ref.rollforward.desde, ref.rollforward.hasta, ref.rollforward.series) == ("2025", "2026T2", "segunda_mano")
    assert ref.rollforward_error is None


@requires_test_db
def test_el_fallback_al_distrito_tambien_se_actualiza(prices):
    import_ipv(prices, SAMPLE, "muestra.json")
    ref = get_reference_price(prices, zone_id(prices, "barrio", "012"), update_to="latest")        # Embajadores: sin dato
    assert (ref.level, ref.fallback_reason, ref.price_m2) == ("distrito", "sin_dato", 7171.36)
    assert ref.updated_price_m2 == pytest.approx(7171.36 * 1.122)


@requires_test_db
def test_update_to_acepta_un_trimestre_una_fecha_y_otra_serie(prices):
    import_ipv(prices, SAMPLE, "muestra.json")
    zid = zone_id(prices, "barrio", "011")
    assert get_reference_price(prices, zid, update_to="2025T4").rollforward.factor == pytest.approx(101.8 / 100.0)
    assert get_reference_price(prices, zid, update_to=date(2026, 3, 31)).rollforward.hasta == "2026T1"
    assert get_reference_price(prices, zid, update_to="latest", index_series="general").rollforward.factor == pytest.approx(1.11)


@requires_test_db
def test_sin_indices_cargados_devuelve_el_original_y_el_motivo(prices):
    ref = get_reference_price(prices, zone_id(prices, "barrio", "011"), update_to="latest")
    assert ref.price_m2 == 6954.58 and ref.updated_price_m2 is None and ref.rollforward is None
    assert "no hay índices" in ref.rollforward_error


@requires_test_db
def test_si_el_ano_del_precio_no_esta_completo_en_el_indice_avisa(prices):
    import_ipv(prices, SAMPLE, "muestra.json")
    with_2026 = csv_bytes(mini_lines() + [("01. Centro;01. Centro;2026", "7.900,00"), ("01. Centro;011. Palacio;2026", "7.500,00")])
    import_registral(prices, with_2026, "hasta_2026.csv")
    ref = get_reference_price(prices, zone_id(prices, "barrio", "011"), update_to="latest")
    assert ref.price_m2 == 7500.0 and ref.period == date(2026, 12, 31)
    assert ref.updated_price_m2 is None and "2026 no está completo" in ref.rollforward_error


@requires_test_db
def test_sin_zona_o_sin_precio_sigue_siendo_none_con_update_to(prices):
    import_ipv(prices, SAMPLE, "muestra.json")
    assert get_reference_price(prices, ZoneMatch(unscorable_reason="no_zone"), update_to="latest") is None
    assert get_reference_price(prices, 999_999, update_to="latest") is None


@requires_test_db
def test_la_actualizacion_usa_la_revision_mas_reciente_del_indice(prices):
    import_ipv(prices, SAMPLE, "v1.json")
    payload = sample_payload()
    next(s for s in payload if s["Nombre"].endswith("segunda mano. Índice."))["Data"][-1]["Valor"] = 120.0
    import_ipv(prices, as_bytes(payload), "v2.json")
    ref = get_reference_price(prices, zone_id(prices, "distrito", "01"), update_to="latest")
    assert ref.rollforward.index_to == 120.0 and ref.updated_price_m2 == pytest.approx(7171.36 * 1.2)


# ── El script ──────────────────────────────────────────────────────────────────

@requires_test_db
def test_script_dry_run_muestra_series_y_factor_sin_escribir(db, capsys):
    from scripts import load_ipv
    assert load_ipv.main(["--file", str(SAMPLE_PATH)]) == 0
    out = capsys.readouterr().out
    assert "'Madrid, Comunidad de. Vivienda de segunda mano. Índice.'" in out
    assert "puntos leídos: 30 (2024T1 … 2026T2) · a insertar: 30" in out
    assert "factor 2025 → 2026T2: ×1.1220" in out and "[dry-run]" in out
    assert counts(db) == (0, 0)


@requires_test_db
def test_script_confirm_escribe_y_la_segunda_vez_no_hace_nada(db, capsys):
    from scripts import load_ipv
    assert load_ipv.main(["--file", str(SAMPLE_PATH), "--confirm"]) == 0
    assert "OK: carga #" in capsys.readouterr().out
    db.expire_all()
    assert counts(db) == (1, 30)
    assert load_ipv.main(["--file", str(SAMPLE_PATH), "--confirm"]) == 0
    assert "ya importado" in capsys.readouterr().out
    db.expire_all()
    assert counts(db) == (1, 30)


@requires_test_db
def test_script_desde_la_api_usa_el_cliente_inyectado(db, capsys):
    from scripts import load_ipv
    http = FakeHttp(FakeResponse(content=SAMPLE))
    assert load_ipv.main(["--table", "25171", "--nult", "10", "--confirm"], http=http) == 0
    assert http.calls[0][0].endswith("/DATOS_TABLA/25171") and http.calls[0][1]["params"] == {"nult": 10}
    db.expire_all()
    from backend.models.market import MarketImport
    assert db.query(MarketImport).one().file_name == "tempus:25171"


@requires_test_db
def test_script_si_la_api_falla_no_escribe_y_sale_con_error(db, capsys):
    from scripts import load_ipv
    http = FakeHttp(FakeResponse(503, b"Service Temporarily Unavailable"))
    assert load_ipv.main(["--confirm"], http=http) == load_ipv.EXIT_FETCH
    out = capsys.readouterr().out
    assert "ERROR" in out and "No se escribió nada" in out
    assert counts(db) == (0, 0)


@requires_test_db
def test_script_con_archivos_invalidos(db, tmp_path, capsys):
    from scripts import load_ipv
    nothing = tmp_path / "otra_region.json"
    nothing.write_text(json.dumps([serie("Cataluña. General. Índice.", [quarter_item(2025, 1, 100.0)])]))
    broken = tmp_path / "roto.json"
    broken.write_text("{no es json")
    assert load_ipv.main(["--file", str(nothing), "--confirm"]) == load_ipv.EXIT_BAD_INPUT
    assert "ninguna serie de índice" in capsys.readouterr().out
    assert load_ipv.main(["--file", str(broken)]) == load_ipv.EXIT_BAD_INPUT
    assert load_ipv.main(["--file", str(tmp_path / "no_existe.json")]) == load_ipv.EXIT_BAD_INPUT
    assert counts(db) == (0, 0)


def test_el_fixture_no_cambia_por_accidente():
    """Los números de los tests de arriba dependen de este archivo."""
    payload = sample_payload()
    assert len(payload) == 9 and sum(1 for s in payload if classify_series(s["Nombre"])) == 3
    assert deepcopy(payload) == payload
