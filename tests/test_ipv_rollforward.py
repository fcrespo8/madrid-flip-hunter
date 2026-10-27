"""IPV del INE (tabla 79563, base 2025): CSV de la web y JSON de Tempus, market_indices, rollforward_factor y
get_reference_price(update_to=...). Todo offline.

El fixture CSV (tests/data/ine_ipv_79563_sample.csv) es un fragmento REAL de lo que baja de la web del INE. El JSON de
Tempus de estos tests se arma en el test a partir de esos mismos valores reales: la forma sigue la documentación de
Tempus, pero no se vio una respuesta real de la API."""
import ast
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import requests
from sqlalchemy.exc import IntegrityError

from backend.market.importer import import_registral
from backend.market.ipv import (
    ALREADY_IMPORTED, DEFAULT_TABLE_ID, IMPORTED, IPVFetchError, IPVParseError, classify_series, decode_ine_csv,
    fetch_tempus, import_ipv, matched_series_names, parse_ine_csv, parse_ipv_content, parse_tempus_ipv, quarter_end,
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
SAMPLE_PATH = ROOT / "tests" / "data" / "ine_ipv_79563_sample.csv"
REAL_PATH = ROOT / "data" / "raw" / "79563.csv"
SAMPLE = SAMPLE_PATH.read_bytes()

# Valores reales de la Comunidad de Madrid (tabla 79563). 2025 promedia 100 (base 2025).
SM = {"2025T1": 94.057, "2025T2": 98.771, "2025T3": 102.666, "2025T4": 104.506, "2026T1": 107.894, "2026T2": 112.649}
GEN = {"2025T1": 94.468, "2025T2": 99.111, "2025T3": 102.306, "2025T4": 104.115, "2026T1": 107.296, "2026T2": 111.850}
NEW = {"2025T1": 96.866, "2025T2": 101.099, "2025T3": 100.203, "2025T4": 101.833, "2026T1": 103.572, "2026T2": 106.728}
HEADER = "Total Nacional;Comunidades y Ciudades Autónomas;General, vivienda nueva y de segunda mano;Índices y tasas;Periodo;Total"


def csv_text(*rows) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode("cp1252")


def madrid_row(housing, period, value, kind="Índice", region="13 Madrid, Comunidad de"):
    return f"Nacional;{region};{housing};{kind};{period};{value}"


def tempus_item(period, value):
    year, quarter = int(period[:4]), int(period[-1])
    return {"Fecha": 0, "FK_TipoDato": 1, "FK_Periodo": 18 + quarter, "Anyo": year, "Valor": value, "Secreto": False}


def tempus_serie(nombre, values):
    return {"COD": "X", "Nombre": nombre, "Data": [tempus_item(p, v) for p, v in values.items()]}


def tempus_payload():
    """Tempus con los valores reales de arriba, más series que deben ignorarse (otras regiones, variaciones, pesos)."""
    return [
        tempus_serie("Total Nacional. 13 Madrid, Comunidad de. General. Índice.", GEN),
        tempus_serie("Total Nacional. 13 Madrid, Comunidad de. Vivienda nueva. Índice.", NEW),
        tempus_serie("Total Nacional. 13 Madrid, Comunidad de. Vivienda segunda mano. Índice.", SM),
        tempus_serie("Total Nacional. 13 Madrid, Comunidad de. Vivienda segunda mano. Variación anual.", {"2026T2": 14.0}),
        tempus_serie("Total Nacional. 13 Madrid, Comunidad de. General. Variación trimestral.", {"2026T2": 4.2}),
        tempus_serie("Total Nacional. 13 Madrid, Comunidad de. Ponderaciones. Vivienda segunda mano.", {"2026T2": 0.8}),
        tempus_serie("Total Nacional. 09 Cataluña. Vivienda segunda mano. Índice.", {"2026T2": 111.0}),
        tempus_serie("Total Nacional. Vivienda segunda mano. Índice.", {"2026T2": 111.682}),
    ]


def as_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# ── El fixture es un fragmento real ────────────────────────────────────────────

def test_el_fixture_esta_en_cp1252_como_el_que_baja_el_ine():
    with pytest.raises(UnicodeDecodeError):
        SAMPLE.decode("utf-8")                                    # la "Í" de Índice es el byte 0xCD
    assert b"\xcd" in SAMPLE and b"\r\n" in SAMPLE
    assert decode_ine_csv(SAMPLE).splitlines()[0] == HEADER


@pytest.mark.skipif(not REAL_PATH.exists(), reason="data/raw/79563.csv no está")
def test_el_fixture_son_lineas_reales_del_archivo_de_data_raw():
    real = set(decode_ine_csv(REAL_PATH.read_bytes()).splitlines())
    assert all(line in real for line in decode_ine_csv(SAMPLE).splitlines())


# ── CSV de la web del INE ──────────────────────────────────────────────────────

def test_parsea_el_csv_real_y_solo_toma_los_indices_de_madrid():
    parsed = parse_ine_csv(SAMPLE)
    assert parsed.format == "csv" and len(parsed.points) == 18                                 # 3 series × 6 trimestres
    by = {(p.series, p.period): p for p in parsed.points}
    assert by[("segunda_mano", "2026T2")].value == 112.649 and by[("segunda_mano", "2026T2")].period_end == date(2026, 6, 30)
    assert by[("general", "2025T1")].value == 94.468 and by[("nueva", "2025T4")].value == 101.833
    assert {p.value for p in parsed.points}.isdisjoint({111.682, 111.095})                      # filas nacionales: no entran
    assert sum(by[("segunda_mano", f"2025T{q}")].value for q in (1, 2, 3, 4)) / 4 == pytest.approx(100.0)
    assert parsed.series_names["segunda_mano"] == "13 Madrid, Comunidad de / Vivienda segunda mano / Índice"
    assert [p.period for p in parsed.points if p.series == "general"][0] == "2025T1"            # ordenados por período


def test_decode_distingue_cp1252_de_latin1():
    """cp1252 y latin-1 coinciden en tildes y eñes; difieren en 0x80–0x9F (€, comillas y rayas)."""
    assert decode_ine_csv("Total – 5 € “x”".encode("cp1252")) == "Total – 5 € “x”"
    assert decode_ine_csv("Índice;ñ".encode("cp1252")) == "Índice;ñ" == decode_ine_csv("Índice;ñ".encode("utf-8"))


def test_coma_decimal_y_cp1252_o_utf8():
    row = madrid_row("Vivienda segunda mano", "2026T2", "112,649")
    for content in (csv_text(row), ("﻿" + HEADER + "\n" + row + "\n").encode("utf-8"), (HEADER + "\n" + row).encode("utf-8")):
        (point,) = parse_ine_csv(content).points
        assert (point.series, point.period, point.value) == ("segunda_mano", "2026T2", 112.649)


def test_el_csv_ignora_variaciones_otras_comunidades_y_series_desconocidas():
    content = csv_text(
        madrid_row("Vivienda segunda mano", "2026T2", "112,649"),
        madrid_row("Vivienda segunda mano", "2026T2", "12,9", kind="Variación anual"),
        madrid_row("Vivienda segunda mano", "2026T2", "99,0", region="09 Cataluña"),
        madrid_row("Vivienda segunda mano", "2026T2", "99,1", region=""),
        madrid_row("Vivienda de lujo", "2026T2", "150,0"),
    )
    assert [(p.series, p.value) for p in parse_ine_csv(content).points] == [("segunda_mano", 112.649)]


def test_un_trimestre_sin_valor_se_omite():
    content = csv_text(madrid_row("General", "2026T1", "107,296"), madrid_row("General", "2026T2", ".."), madrid_row("General", "2026T3", ""))
    assert [p.period for p in parse_ine_csv(content).points] == ["2026T1"]


@pytest.mark.parametrize("content, message", [
    (b"", "no hay filas"),
    ("Distrito;Barrio;Año\nx;y;z\n".encode(), "cabecera inesperada"),
    ("a;b;c;d;e;f\nNacional;13 Madrid, Comunidad de;General;Índice;2026T2;100,0\n".encode(), "cabecera inesperada"),   # 6 columnas, pero no son las del INE
    (csv_text("Nacional;13 Madrid, Comunidad de;General;Índice;2026T2"), "línea 2: 5 columnas"),
    (csv_text(madrid_row("General", "T2 2026", "100,0")), "línea 2: periodo no válido"),
    (csv_text(madrid_row("General", "2026T5", "100,0")), "periodo no válido"),
    (csv_text(madrid_row("General", "2026T2", "cien")), "línea 2: valor no válido"),
    (csv_text(madrid_row("General", "2026T2", "0,0")), "línea 2: valor fuera de rango"),
    (csv_text(madrid_row("General", "2026T2", "5000,0")), "valor fuera de rango"),
    (csv_text(madrid_row("General", "2026T2", "100,0"), madrid_row("General", "2026T2", "101,0")), "línea 3: la serie 'general' repite 2026T2"),
    (csv_text(madrid_row("General", "2026T2", "100,0", region="09 Cataluña")), "no hay filas de índice de la Comunidad de Madrid"),
    (csv_text(madrid_row("General", "2026T2", "12,0", kind="Variación anual")), "no hay filas de índice"),
])
def test_csv_mal_formado(content, message):
    with pytest.raises(IPVParseError, match=message):
        parse_ine_csv(content)


def test_parse_ipv_content_elige_el_formato_por_el_contenido():
    csv_parsed, json_parsed = parse_ipv_content(SAMPLE), parse_ipv_content(as_bytes(tempus_payload()))
    assert (csv_parsed.format, json_parsed.format) == ("csv", "json")
    assert [(p.series, p.period, p.value) for p in csv_parsed.points] == [(p.series, p.period, p.value) for p in json_parsed.points]
    for bad in (b"[no es json", b"{ tampoco"):
        with pytest.raises(IPVParseError, match="no es JSON"):
            parse_ipv_content(bad)


# ── JSON de Tempus ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("Total Nacional. 13 Madrid, Comunidad de. General. Índice.", "general"),
    ("Total Nacional. 13 Madrid, Comunidad de. Vivienda nueva. Índice.", "nueva"),
    ("Total Nacional. 13 Madrid, Comunidad de. Vivienda segunda mano. Índice.", "segunda_mano"),
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


def test_parsea_tempus_y_se_queda_solo_con_los_indices_de_madrid():
    payload = tempus_payload()
    points = parse_tempus_ipv(payload)
    assert len(points) == 18 and {p.series for p in points} == {"general", "nueva", "segunda_mano"}
    assert next(p for p in points if p.series == "segunda_mano" and p.period == "2026T2").value == 112.649
    assert sorted(matched_series_names(payload)) == ["general", "nueva", "segunda_mano"]


def test_quarter_end():
    assert [quarter_end(2026, q) for q in (1, 2, 3, 4)] == [date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)]


def test_un_trimestre_sin_publicar_se_omite_en_tempus():
    payload = [tempus_serie("Madrid, Comunidad de. General. Índice.", {"2026T1": 107.296, "2026T2": None})]
    assert [p.period for p in parse_tempus_ipv(payload)] == ["2026T1"]


@pytest.mark.parametrize("code, quarter", [(19, 1), (20, 2), (21, 3), (22, 4)])
def test_codigos_de_periodo_de_tempus(code, quarter):
    payload = [{"Nombre": "Madrid, Comunidad de. General. Índice.", "Data": [{"Anyo": 2025, "FK_Periodo": code, "Valor": 100.0}]}]
    assert parse_tempus_ipv(payload)[0].period == f"2025T{quarter}"


@pytest.mark.parametrize("fecha, quarter", [
    (datetime(2025, 7, 1, tzinfo=timezone.utc), 3),
    (datetime(2025, 6, 30, 22, 0, tzinfo=timezone.utc), 3),            # 1 de julio 00:00 en hora de España (CEST)
    (datetime(2024, 12, 31, 23, 0, tzinfo=timezone.utc), 1),           # 1 de enero 00:00 en hora de España (CET)
    (datetime(2025, 9, 30, 0, 0, tzinfo=timezone.utc), 3),
    (datetime(2025, 9, 29, 22, 0, tzinfo=timezone.utc), 3),
])
def test_si_el_codigo_de_periodo_es_desconocido_se_usa_la_fecha(fecha, quarter):
    payload = [{"Nombre": "Madrid, Comunidad de. General. Índice.",
                "Data": [{"Anyo": 2025, "FK_Periodo": 99, "Fecha": int(fecha.timestamp() * 1000), "Valor": 100.0}]}]
    assert parse_tempus_ipv(payload)[0].period == f"2025T{quarter}"


@pytest.mark.parametrize("payload, message", [
    ({"no": "lista"}, "lista de series"),
    ([], "ninguna serie de índice"),
    ([tempus_serie("Cataluña. General. Índice.", {"2025T1": 100.0})], "ninguna serie de índice"),
    ([tempus_serie("Madrid, Comunidad de. General. Índice.", {}), tempus_serie("Madrid, Comunidad de. General. Índice.", {})], "más de una vez"),
    ([{"Nombre": "Madrid, Comunidad de. General. Índice.", "Data": [tempus_item("2025T1", 100.0), tempus_item("2025T1", 101.0)]}], "repite trimestres"),
    ([tempus_serie("Madrid, Comunidad de. General. Índice.", {"2025T1": 0})], "valor no válido"),
    ([tempus_serie("Madrid, Comunidad de. General. Índice.", {"2025T1": 5000.0})], "valor no válido"),
    ([tempus_serie("Madrid, Comunidad de. General. Índice.", {"2025T1": "100"})], "valor no válido"),
    ([tempus_serie("Madrid, Comunidad de. General. Índice.", {"2025T1": True})], "valor no válido"),
    ([{"Nombre": "Madrid, Comunidad de. General. Índice.", "Data": [{"FK_Periodo": 19, "Valor": 100.0}]}], "sin año"),
    ([{"Nombre": "Madrid, Comunidad de. General. Índice.", "Data": [{"Anyo": 2025, "FK_Periodo": 28, "Valor": 100.0}]}], "no se pudo determinar el trimestre"),
])
def test_tempus_mal_formado(payload, message):
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


def test_la_tabla_por_defecto_es_la_79563():
    assert DEFAULT_TABLE_ID == 79563
    http = FakeHttp(FakeResponse(content=b'[{"a": 1}]'))
    assert fetch_tempus(nult=12, timeout=7, http=http) == b'[{"a": 1}]'
    (url, kwargs), = http.calls
    assert url == "https://servicios.ine.es/wstempus/js/es/DATOS_TABLA/79563"
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


def test_labels_y_latest_quarter():
    assert Period(2025).label == "2025" and Period(2026, 2).label == "2026T2"
    assert latest_quarter(SM) == Period(2026, 2)
    assert latest_quarter({"2025T4": 1.0, "2026T1": 2.0, "2024T4": 3.0}) == Period(2026, 1)
    assert latest_quarter({}) is None


def test_index_value_trimestral_y_anual():
    assert index_value(SM, Period(2026, 2)) == 112.649
    assert index_value(SM, Period(2025)) == pytest.approx(100.0)


@pytest.mark.parametrize("period, message", [(Period(2026), r"no está completo.*2026T3, 2026T4"), (Period(2026, 3), "2026T3"), (Period(2019), "2019T1")])
def test_index_value_sin_dato(period, message):
    with pytest.raises(IndexNotAvailable, match=message):
        index_value(SM, period)


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


@requires_test_db
def test_la_primera_carga_del_csv_inserta_los_puntos_y_registra_la_importacion(db):
    from backend.models.market import MarketImport, MarketIndex
    result = import_ipv(db, SAMPLE, "79563.csv")
    db.commit()
    assert (result.status, result.points_read, result.inserted, result.unchanged, result.revised) == (IMPORTED, 18, 18, 0, 0)
    assert (result.period_from, result.period_to) == ("2025T1", "2026T2")
    assert counts(db) == (1, 18)
    record = db.query(MarketImport).one()
    assert (record.source, record.file_name, record.period_label, record.row_count, record.skipped_count) == ("ine_ipv", "79563.csv", "2025T1-2026T2", 18, 0)
    assert "Comunidad de Madrid" in record.notes and "formato csv" in record.notes and "segunda_mano" in record.notes
    row = db.query(MarketIndex).filter_by(series="segunda_mano", period="2026T2").one()
    assert (row.value, row.period_end, row.geography, row.source, row.import_id) == (112.649, date(2026, 6, 30), "Comunidad de Madrid", "ine_ipv", record.id)


@requires_test_db
def test_el_mismo_contenido_dos_veces_no_hace_nada(db):
    first = import_ipv(db, SAMPLE, "79563.csv")
    db.commit()
    again = import_ipv(db, SAMPLE, "otro_nombre.csv")
    assert (again.status, again.import_id, again.inserted, again.changed) == (ALREADY_IMPORTED, first.import_id, 0, False)
    assert counts(db) == (1, 18)


@requires_test_db
def test_los_mismos_valores_por_la_api_despues_del_csv_no_duplican_filas(db):
    import_ipv(db, SAMPLE, "79563.csv")
    result = import_ipv(db, as_bytes(tempus_payload()), "tempus:79563")
    assert (result.status, result.inserted, result.unchanged) == (IMPORTED, 0, 18)
    assert counts(db) == (2, 18)


@requires_test_db
def test_una_carga_nueva_solo_agrega_los_puntos_nuevos_o_revisados(db):
    import_ipv(db, SAMPLE, "v1.csv")
    newer = csv_text(madrid_row("Vivienda segunda mano", "2026T2", "113,000"),        # el INE revisó 2026T2
                     madrid_row("Vivienda segunda mano", "2026T3", "115,500"),        # y publicó 2026T3
                     madrid_row("Vivienda segunda mano", "2026T1", "107,894"))        # sin cambios
    result = import_ipv(db, newer, "v2.csv")
    db.commit()
    assert (result.status, result.points_read, result.inserted, result.unchanged, result.revised) == (IMPORTED, 3, 2, 1, 1)
    assert counts(db) == (2, 20)
    from backend.models.market import MarketIndex
    versions = db.query(MarketIndex).filter_by(series="segunda_mano", period="2026T2").order_by(MarketIndex.import_id).all()
    assert [v.value for v in versions] == [112.649, 113.0]                              # la versión vieja se conserva
    assert rollforward_factor(db, 2025, "2026T2").index_to == 113.0
    assert rollforward_factor(db, 2025).hasta == "2026T3"


@requires_test_db
@pytest.mark.parametrize("content", [b"no es json ni csv", b"[]", b'{"a": 1}', b""])
def test_un_contenido_invalido_no_escribe_nada(db, content):
    with pytest.raises(IPVParseError):
        import_ipv(db, content, "malo")
    assert counts(db) == (0, 0)


@requires_test_db
def test_restricciones_de_la_base(db):
    from backend.models.market import MarketIndex
    result = import_ipv(db, SAMPLE, "79563.csv")
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
    fails(period="2025T1", period_end=date(2025, 3, 31))                                # (punto, carga) repetido
    assert counts(db) == (1, 18)


# ── rollforward_factor ─────────────────────────────────────────────────────────

@requires_test_db
def test_factor_2025_a_2026T2_de_madrid_segunda_mano(db):
    import_ipv(db, SAMPLE, "79563.csv")
    rf = rollforward_factor(db, 2025)
    assert (rf.desde, rf.hasta, rf.series, rf.geography) == ("2025", "2026T2", "segunda_mano", "Comunidad de Madrid")
    assert rf.index_from == pytest.approx(100.0) and rf.index_to == 112.649
    assert rf.factor == pytest.approx(1.12649) and round(rf.factor, 4) == 1.1265            # ~1,1265
    assert float(rf) == rf.factor and rf.apply(6954.58) == pytest.approx(6954.58 * 1.12649)


@requires_test_db
def test_factor_entre_trimestres_fechas_y_series(db):
    import_ipv(db, SAMPLE, "79563.csv")
    assert rollforward_factor(db, "2025T2", "2026T2").factor == pytest.approx(112.649 / 98.771)
    assert rollforward_factor(db, date(2025, 5, 3), date(2026, 6, 30)).factor == pytest.approx(112.649 / 98.771)
    assert rollforward_factor(db, 2025, "2026T1").factor == pytest.approx(107.894 / 100.0)
    assert rollforward_factor(db, 2025, series="general").factor == pytest.approx(1.1185)
    assert rollforward_factor(db, 2025, series="nueva").factor == pytest.approx(106.728 / 100.00025)
    assert rollforward_factor(db, "2026T2", "2025T2").factor < 1                       # hacia atrás también
    assert rollforward_factor(db, 2025, 2025).factor == pytest.approx(1.0)


@requires_test_db
@pytest.mark.parametrize("desde, hasta, message", [
    (2026, None, r"2026 no está completo.*2026T3"),                 # el año de origen no cerró
    (2024, None, "2024T1"),                                         # el archivo empieza en 2025T1
    (2025, "2026T4", "2026T4"),                                     # no se extrapola más allá del último dato
    (2025, 2026, "2026 no está completo"),
])
def test_factor_sin_indice_disponible(db, desde, hasta, message):
    import_ipv(db, SAMPLE, "79563.csv")
    with pytest.raises(IndexNotAvailable, match=message):
        rollforward_factor(db, desde, hasta)


@requires_test_db
def test_factor_sin_nada_cargado_o_con_argumentos_invalidos(db):
    with pytest.raises(IndexNotAvailable, match="no hay índices segunda_mano"):
        rollforward_factor(db, 2025)
    import_ipv(db, SAMPLE, "79563.csv")
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
    import_ipv(prices, SAMPLE, "79563.csv")
    ref = get_reference_price(prices, zone_id(prices, "barrio", "011"), update_to="latest")
    assert ref.price_m2 == 6954.58 and ref.level == "barrio"                              # el original queda intacto
    assert ref.updated_price_m2 == pytest.approx(6954.58 * 1.12649)
    assert ref.rollforward.factor == pytest.approx(1.12649)
    assert (ref.rollforward.desde, ref.rollforward.hasta, ref.rollforward.series) == ("2025", "2026T2", "segunda_mano")
    assert ref.rollforward_error is None


@requires_test_db
def test_el_fallback_al_distrito_tambien_se_actualiza(prices):
    import_ipv(prices, SAMPLE, "79563.csv")
    ref = get_reference_price(prices, zone_id(prices, "barrio", "012"), update_to="latest")        # Embajadores: sin dato
    assert (ref.level, ref.fallback_reason, ref.price_m2) == ("distrito", "sin_dato", 7171.36)
    assert ref.updated_price_m2 == pytest.approx(7171.36 * 1.12649)


@requires_test_db
def test_update_to_acepta_un_trimestre_una_fecha_y_otra_serie(prices):
    import_ipv(prices, SAMPLE, "79563.csv")
    zid = zone_id(prices, "barrio", "011")
    assert get_reference_price(prices, zid, update_to="2025T4").rollforward.factor == pytest.approx(1.04506)
    assert get_reference_price(prices, zid, update_to=date(2026, 3, 31)).rollforward.hasta == "2026T1"
    assert get_reference_price(prices, zid, update_to="latest", index_series="general").rollforward.factor == pytest.approx(1.1185)


@requires_test_db
def test_sin_indices_cargados_devuelve_el_original_y_el_motivo(prices):
    ref = get_reference_price(prices, zone_id(prices, "barrio", "011"), update_to="latest")
    assert ref.price_m2 == 6954.58 and ref.updated_price_m2 is None and ref.rollforward is None
    assert "no hay índices" in ref.rollforward_error


@requires_test_db
def test_si_el_ano_del_precio_no_esta_completo_en_el_indice_avisa(prices):
    import_ipv(prices, SAMPLE, "79563.csv")
    with_2026 = csv_bytes(mini_lines() + [("01. Centro;01. Centro;2026", "7.900,00"), ("01. Centro;011. Palacio;2026", "7.500,00")])
    import_registral(prices, with_2026, "hasta_2026.csv")
    ref = get_reference_price(prices, zone_id(prices, "barrio", "011"), update_to="latest")
    assert ref.price_m2 == 7500.0 and ref.period == date(2026, 12, 31)
    assert ref.updated_price_m2 is None and "2026 no está completo" in ref.rollforward_error


@requires_test_db
def test_sin_zona_o_sin_precio_sigue_siendo_none_con_update_to(prices):
    import_ipv(prices, SAMPLE, "79563.csv")
    assert get_reference_price(prices, ZoneMatch(unscorable_reason="no_zone"), update_to="latest") is None
    assert get_reference_price(prices, 999_999, update_to="latest") is None


@requires_test_db
def test_la_actualizacion_usa_la_revision_mas_reciente_del_indice(prices):
    import_ipv(prices, SAMPLE, "v1.csv")
    import_ipv(prices, csv_text(madrid_row("Vivienda segunda mano", "2026T2", "120,000")), "v2.csv")
    ref = get_reference_price(prices, zone_id(prices, "distrito", "01"), update_to="latest")
    assert ref.rollforward.index_to == 120.0 and ref.updated_price_m2 == pytest.approx(7171.36 * 1.2)


# ── El script ──────────────────────────────────────────────────────────────────

@requires_test_db
def test_script_dry_run_con_el_csv_muestra_series_y_factor_sin_escribir(db, capsys):
    from scripts import load_ipv
    assert load_ipv.main(["--file", str(SAMPLE_PATH)]) == 0
    out = capsys.readouterr().out
    assert "formato csv" in out and "'13 Madrid, Comunidad de / Vivienda segunda mano / Índice'" in out
    assert "puntos leídos: 18 (2025T1 … 2026T2) · a insertar: 18" in out
    assert "factor 2025 → 2026T2: ×1.1265" in out and "[dry-run]" in out
    assert counts(db) == (0, 0)


@requires_test_db
def test_script_dry_run_con_un_json_de_tempus(db, tmp_path, capsys):
    from scripts import load_ipv
    path = tmp_path / "tempus.json"
    path.write_bytes(as_bytes(tempus_payload()))
    assert load_ipv.main(["--file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "formato json" in out and "Total Nacional. 13 Madrid, Comunidad de. Vivienda segunda mano. Índice." in out
    assert "×1.1265" in out and counts(db) == (0, 0)


@requires_test_db
def test_script_confirm_escribe_y_la_segunda_vez_no_hace_nada(db, capsys):
    from scripts import load_ipv
    assert load_ipv.main(["--file", str(SAMPLE_PATH), "--confirm"]) == 0
    assert "OK: carga #" in capsys.readouterr().out
    db.expire_all()
    assert counts(db) == (1, 18)
    assert load_ipv.main(["--file", str(SAMPLE_PATH), "--confirm"]) == 0
    assert "ya importado" in capsys.readouterr().out
    db.expire_all()
    assert counts(db) == (1, 18)


@requires_test_db
def test_script_desde_la_api_pide_la_tabla_79563(db):
    from scripts import load_ipv
    http = FakeHttp(FakeResponse(content=as_bytes(tempus_payload())))
    assert load_ipv.main(["--nult", "10", "--confirm"], http=http) == 0
    assert http.calls[0][0].endswith("/DATOS_TABLA/79563") and http.calls[0][1]["params"] == {"nult": 10}
    db.expire_all()
    from backend.models.market import MarketImport
    assert db.query(MarketImport).one().file_name == "tempus:79563"


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
    other_region = tmp_path / "cataluna.csv"
    other_region.write_bytes(csv_text(madrid_row("General", "2025T1", "100,0", region="09 Cataluña")))
    broken = tmp_path / "roto.json"
    broken.write_text("{no es json")
    assert load_ipv.main(["--file", str(other_region), "--confirm"]) == load_ipv.EXIT_BAD_INPUT
    assert "no hay filas de índice de la Comunidad de Madrid" in capsys.readouterr().out
    assert load_ipv.main(["--file", str(broken)]) == load_ipv.EXIT_BAD_INPUT
    assert load_ipv.main(["--file", str(tmp_path / "no_existe.csv")]) == load_ipv.EXIT_BAD_INPUT
    assert counts(db) == (0, 0)


# ── El archivo real de data/raw ────────────────────────────────────────────────

@requires_test_db
@pytest.mark.skipif(not REAL_PATH.exists(), reason="data/raw/79563.csv no está")
def test_con_el_archivo_real_el_factor_de_madrid_segunda_mano_es_1_1265(db):
    import_ipv(db, REAL_PATH.read_bytes(), "79563.csv")
    rf = rollforward_factor(db, 2025)
    assert (rf.hasta, rf.series) == ("2026T2", "segunda_mano")
    assert round(rf.factor, 4) == 1.1265
