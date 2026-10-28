"""zone_indicators y el importador del Panel de indicadores de datos.madrid.es (9 indicadores fijos). Todo offline.

tests/data/panel_indicadores_sample.csv son líneas REALES del CSV acumulado del panel (ciudad, distrito 1 Centro, barrio 011
Palacio y barrio 212 Aeropuerto, todas las ediciones de los 9 indicadores) más algunos señuelos que no se deben importar."""
import ast
import json
import re
from pathlib import Path

import pytest
import requests
from sqlalchemy.exc import IntegrityError

from backend.market.panel import (
    ALREADY_IMPORTED, BY_NAME, IMPORTED, INDICATORS, PanelFetchError, PanelImportError, PanelIndicatorMissing,
    PanelParseError, decode_panel, fetch_panel, get_zone_indicators, import_panel, parse_number, parse_panel_csv,
)
from backend.market.registral import parse_registral_text
from backend.market.resolver import ZoneMatch
from backend.market.zones import normalize_zone_name, seed_zones, zone_specs_from_rows
from tests.conftest import requires_test_db

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = ROOT / "tests" / "data" / "panel_indicadores_sample.csv"
SAMPLE = SAMPLE_PATH.read_bytes()
SAMPLE_TEXT = SAMPLE.decode("utf-8-sig")
REGISTRAL_HEADER = "Distrito;Barrio;Año;Precio medio y números índice;Tipo de vivienda;Total\n"
ZONES_CSV = (REGISTRAL_HEADER + "".join(f"{z};Euros/m2;Usadas;1.000,00\n" for z in [
    "Ciudad de Madrid;Ciudad de Madrid;2025", "01. Centro;01. Centro;2025", "01. Centro;011. Palacio;2025",
    "21. Barajas;21. Barajas;2025", "21. Barajas;212. Aeropuerto;2025"])).encode()

# Valores reales (última edición) de las dos zonas del fixture.
PALACIO = {"renta_neta_hogar": (2023, 48025.0), "valor_catastral_medio": (2025, 136594.48), "anio_medio_construccion": (2025, 1928.0),
           "tasa_paro": (2026, 5.41), "crecimiento_poblacion": (2025, -2.493), "tamano_medio_hogar": (2025, 1.94),
           "envejecimiento": (2025, 19.88), "locales_cerrados": (2026, 485.0), "densidad_poblacion": (2026, 159.2625349)}
AEROPUERTO = {"renta_neta_hogar": (2023, 33236.0), "valor_catastral_medio": (2025, 62765.3), "anio_medio_construccion": (2025, 1962.0),
              "tasa_paro": (2026, 5.73), "crecimiento_poblacion": (2025, -1.697), "tamano_medio_hogar": (2025, 2.72),
              "envejecimiento": (2025, 15.04), "locales_cerrados": (2026, 172.0), "densidad_poblacion": (2026, 0.649930726)}


def lines_without(text: str, *panel_names: str) -> str:
    drop = {normalize_zone_name(n) for n in panel_names}
    out = [text.splitlines()[0]] + [line for line in text.splitlines()[1:] if normalize_zone_name(line.split(";")[16]) not in drop]
    return "\r\n".join(out) + "\r\n"


def rename(text: str, old: str, new: str) -> str:
    return text.replace(f";{old}", f";{new}")


HEADER = "Orden;Periodo panel;ciudad;cod_distrito;distrito;cod_barrio;barrio;año;fecha_indicador;fuente_indicador;categoría_1;categoría_2;indicador_nivel1;indicador_nivel2;indicador_nivel3;unidad_indicador;indicador_completo;valor_indicador"


def row(edition, distrito, barrio, year, indicator, value, unit="Tasa"):
    return f"1;{edition};Ciudad de Madrid;{distrito};X;{barrio};X;{year};01/01/{year};Estadística;Cat;;N1;;;{unit};{indicator};{value}"


def panel(*rows) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode("utf-8-sig")


PARO = "Tasa absoluta de paro registrado (febrero)"


# ── La lista fija ──────────────────────────────────────────────────────────────

def test_la_lista_tiene_los_nueve_indicadores_sin_nacionalidad():
    assert [s.key for s in INDICATORS] == [
        "renta_neta_hogar", "valor_catastral_medio", "anio_medio_construccion", "tasa_paro", "crecimiento_poblacion",
        "tamano_medio_hogar", "envejecimiento", "locales_cerrados", "densidad_poblacion"]
    assert len({s.name_norm for s in INDICATORS}) == 9 and len(BY_NAME) == 9
    assert all(len(s.key) <= 40 and len(s.unit) <= 30 and s.meaning for s in INDICATORS)
    assert not [s for s in INDICATORS if re.search(r"nacionalidad|extranjer|migrant|origen|pais", s.name_norm + s.key)]


@pytest.mark.parametrize("raw, expected", [
    ("523", 523.0), ("1.303", 1303.0), ("43,89", 43.89), ("136.594,48", 136594.48), ("-0,71", -0.71), ("0", 0.0),
    ("19,18872267", 19.18872267), (" 1927 ", 1927.0), ("1.234.567", 1234567.0),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["abc", "1.5", "12.34", "1,2,3", "", "1..000", "--1"])
def test_parse_number_rechaza_lo_ambiguo_o_invalido(raw):
    with pytest.raises(ValueError):
        parse_number(raw)


# ── Parser sobre el fragmento real ─────────────────────────────────────────────

def by_key(parsed):
    return {(v.indicator, v.zone, v.year): v for v in parsed.values}


def test_el_fragmento_real_trae_los_nueve_indicadores_a_nivel_barrio():
    parsed = parse_panel_csv(SAMPLE)
    assert parsed.missing == [] and set(parsed.barrio_rows) == {s.key for s in INDICATORS}
    assert {v.zone for v in parsed.values} == {("ciudad", None), ("distrito", "01"), ("barrio", "011"), ("barrio", "212")}
    assert {v.indicator for v in parsed.values} == {s.key for s in INDICATORS}


@pytest.mark.parametrize("zone, expected", [(("barrio", "011"), PALACIO), (("barrio", "212"), AEROPUERTO)])
def test_el_ultimo_valor_de_cada_indicador_es_el_real(zone, expected):
    values = by_key(parse_panel_csv(SAMPLE))
    for key, (year, value) in expected.items():
        assert values[(key, zone, year)].value == pytest.approx(value), key
        assert max(y for (k, z, y) in values if k == key and z == zone) == year, key           # y es el año más nuevo


def test_los_codigos_se_completan_con_ceros():
    zones = {v.zone for v in parse_panel_csv(SAMPLE).values}
    assert ("barrio", "011") in zones and ("distrito", "01") in zones                            # en el archivo: 11 y 1


def test_si_dos_ediciones_traen_el_mismo_dato_vale_la_mas_nueva():
    parsed = parse_panel_csv(SAMPLE)
    value = by_key(parsed)[("anio_medio_construccion", ("barrio", "011"), 2020)]
    assert (value.value, value.edition) == (1928.0, 2022)                                         # la edición 2021 decía 1927
    assert parsed.superseded > 0


def test_ignora_los_indicadores_fuera_de_la_lista_y_los_recuerda_para_sugerir():
    parsed = parse_panel_csv(SAMPLE)
    assert not [v for v in parsed.values if "nacion" in v.indicator]
    assert "personas con nacionalidad extranjera" in parsed.other_names                           # estaba en el archivo: no se importa
    assert "edad media de la poblacion" in parsed.other_names


def test_el_nombre_se_compara_normalizado():
    text = rename(SAMPLE_TEXT, "Tamaño medio del hogar", "  TAMANO   MEDIO del HOGAR ")
    assert parse_panel_csv(text.encode()).missing == []


def test_el_archivo_escribe_las_unidades_de_varias_formas():
    """Por eso la unidad que se guarda es la canónica de cada indicador, no la del archivo (ver test de la base)."""
    assert "Proporción" in SAMPLE_TEXT and "Media   " in SAMPLE_TEXT


def test_se_detecta_un_indicador_que_desaparece_y_se_sugiere_el_parecido():
    gone = parse_panel_csv(lines_without(SAMPLE_TEXT, PARO).encode())
    assert [s.key for s in gone.missing] == ["tasa_paro"]
    renamed = parse_panel_csv(rename(SAMPLE_TEXT, PARO, "Tasa absoluta de paro registrado (marzo)").encode())
    assert [s.key for s in renamed.missing] == ["tasa_paro"]
    assert renamed.suggestions()["tasa_paro"] == ["tasa absoluta de paro registrado marzo"]


def test_un_indicador_solo_a_nivel_distrito_cuenta_como_faltante():
    only_district = panel(row(2026, "1", "", 2026, PARO, "5,0"))
    parsed = parse_panel_csv(only_district)
    assert "tasa_paro" not in parsed.barrio_rows and "tasa_paro" in [s.key for s in parsed.missing]


@pytest.mark.parametrize("content, message", [
    (b"", "vacío"),
    ("a;b;c\n1;2;3\n".encode(), "faltan columnas"),
    (panel(row(2026, "1", "11", 2026, PARO, "abc")), "línea 2: .*número"),
    (panel(row(2026, "1", "11", 2026, PARO, "1.5")), "número ambiguo"),
    (panel(row(2026, "1", "x1", 2026, PARO, "1,5")), "cod_barrio no válido"),
    (panel(row(2026, "uno", "", 2026, PARO, "1,5")), "cod_distrito no válido"),
    (panel(row("dos", "1", "11", 2026, PARO, "1,5")), "línea 2"),
    (panel(row(2026, "1", "11", "20x6", PARO, "1,5")), "línea 2"),
    (panel(row(2026, "1", "11", 2026, PARO, "1,5"), row(2026, "1", "11", 2026, PARO, "9,9")), "repetido en la edición 2026"),
    ((HEADER + "\r\n1;2\r\n").encode(), "línea 2: 2 columnas"),
])
def test_csv_mal_formado(content, message):
    with pytest.raises(PanelParseError, match=message):
        parse_panel_csv(content)


def test_filas_sin_ano_o_sin_valor_se_cuentan_y_se_omiten():
    parsed = parse_panel_csv(panel(row(2026, "1", "11", "", PARO, "1,5"), row(2026, "1", "11", 2026, PARO, ""), row(2026, "1", "11", 2025, PARO, "6,0")))
    assert (parsed.skipped_no_year, parsed.skipped_no_value, len(parsed.values)) == (1, 1, 1)


def test_el_mismo_dato_repetido_en_la_misma_edicion_no_es_un_error():
    parsed = parse_panel_csv(panel(row(2026, "1", "11", 2026, PARO, "1,5"), row(2026, "1", "11", 2026, PARO, "1,5")))
    assert len(parsed.values) == 1


def test_decodifica_utf8_con_o_sin_bom_y_cp1252():
    assert decode_panel("año;ñ".encode("utf-8-sig")) == "año;ñ" == decode_panel("año;ñ".encode("utf-8"))
    assert decode_panel("año;ñ".encode("cp1252")) == "año;ñ"


# ── fetch_panel (sin red) ──────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status=200, content=b"", payload=None):
        self.status_code, self.content, self._payload = status, content, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload


class FakeHttp:
    def __init__(self, responses=None, error=None):
        self.responses, self.error, self.calls = list(responses or []), error, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.responses.pop(0)


def meta(*resources):
    return FakeResponse(payload={"result": {"resources": list(resources)}})


RES_CSV = {"format": "CSV", "url": "https://datos.madrid.es/x/download/acumulado.csv"}


def test_fetch_panel_resuelve_la_url_con_ckan_y_baja_el_csv():
    http = FakeHttp([meta({"format": "XLSX", "url": "https://x/a.xlsx"}, {"format": "CSV", "url": "https://x/otro.csv"}, RES_CSV), FakeResponse(content=SAMPLE)])
    content, url = fetch_panel(http=http)
    assert (content, url) == (SAMPLE, RES_CSV["url"])
    assert http.calls[0][0].endswith("package_show?id=300087-0-indicadores-distritos") and http.calls[1][0] == RES_CSV["url"]


def test_fetch_panel_usa_el_primer_csv_si_ninguno_se_llama_acumulado():
    other = {"format": "csv", "url": "https://x/panel.csv"}
    assert fetch_panel(http=FakeHttp([meta(other), FakeResponse(content=b"x")]))[1] == "https://x/panel.csv"


@pytest.mark.parametrize("http", [
    FakeHttp([FakeResponse(503)]),
    FakeHttp(error=requests.Timeout("lento")),
    FakeHttp([meta({"format": "PDF", "url": "https://x/a.pdf"})]),
    FakeHttp([FakeResponse(payload={"sin": "result"})]),
    FakeHttp([FakeResponse(content=b"<html>")]),
    FakeHttp([meta(RES_CSV), FakeResponse(content=b"")]),
    FakeHttp([meta(RES_CSV), FakeResponse(500)]),
])
def test_fetch_panel_falla_con_un_error_claro(http):
    with pytest.raises(PanelFetchError):
        fetch_panel(http=http)


# ── Modelo y migración ─────────────────────────────────────────────────────────

def _migration_tree():
    path = next((ROOT / "alembic" / "versions").glob("*_add_zone_indicators.py"))
    return ast.parse(path.read_text())


def test_modelo_y_migracion_coinciden():
    from backend.models.market import ZoneIndicator
    columns = set(ZoneIndicator.__table__.c.keys())
    assert columns == {"id", "zone_id", "indicator", "year", "value", "unit", "import_id"}
    assert not any(c.nullable for c in ZoneIndicator.__table__.c)
    created = {}
    for node in ast.walk(_migration_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_table":
            created[node.args[0].value] = {c.args[0].value for c in node.args[1:]
                                           if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "Column"}
    assert created == {"zone_indicators": columns}


def test_la_migracion_es_la_cabeza_y_se_deshace():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert script.get_revision("c0d5f8b2e4a6").down_revision == "b9c4e7a1d3f8"
    down = next(f for f in _migration_tree().body if isinstance(f, ast.FunctionDef) and f.name == "downgrade")
    assert [n.args[0].value for n in ast.walk(down) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "drop_table"] == ["zone_indicators"]


# ── Contra la base de prueba ───────────────────────────────────────────────────

@pytest.fixture
def db():
    from backend.models.database import SessionLocal
    from backend.models.market import MarketImport, MarketIndex, MarketPrice, ZoneIndicator
    from backend.models.zone import Zone, ZoneAlias

    def wipe(session):
        session.query(ZoneIndicator).delete()
        session.query(MarketIndex).delete()
        session.query(MarketPrice).delete()
        session.query(MarketImport).delete()
        session.query(ZoneAlias).delete()
        session.query(Zone).filter(Zone.parent_id.is_not(None)).delete()
        session.query(Zone).delete()
        session.commit()

    session = SessionLocal()
    wipe(session)
    seed_zones(session, zone_specs_from_rows(parse_registral_text(ZONES_CSV.decode())))
    session.commit()
    yield session
    session.rollback()
    wipe(session)
    session.close()


def zid(db, level, code):
    from backend.models.zone import Zone
    return db.query(Zone).filter_by(level=level, code=code).one().id


def counts(db):
    from backend.models.market import MarketImport, ZoneIndicator
    return db.query(MarketImport).count(), db.query(ZoneIndicator).count()


@requires_test_db
def test_la_primera_carga_inserta_los_valores_y_registra_la_importacion(db):
    from backend.models.market import MarketImport, ZoneIndicator
    result = import_panel(db, SAMPLE, "acumulado.csv")
    db.commit()
    parsed = parse_panel_csv(SAMPLE)
    assert (result.status, result.values_read, result.inserted, result.unchanged, result.revised) == (IMPORTED, len(parsed.values), len(parsed.values), 0, 0)
    assert result.missing == [] and result.superseded == parsed.superseded > 0
    assert counts(db) == (1, len(parsed.values))
    record = db.query(MarketImport).one()
    assert (record.source, record.file_name, record.row_count, record.skipped_count) == ("madrid_panel", "acumulado.csv", len(parsed.values), 0)
    assert record.period_label == f"{result.period_from}-{result.period_to}" and result.period_from < result.period_to
    notes = json.loads(record.notes)
    assert notes["faltantes"] == [] and set(notes["indicadores"]) == {s.key for s in INDICATORS}
    assert {r.unit for r in db.query(ZoneIndicator)} == {s.unit for s in INDICATORS}
    assert all(info["barrios"] == 2 for info in result.per_indicator.values())                    # Palacio y Aeropuerto


@requires_test_db
def test_los_valores_quedan_en_la_zona_correcta(db):
    import_panel(db, SAMPLE, "acumulado.csv")
    palacio = get_zone_indicators(db, zid(db, "barrio", "011"))
    aeropuerto = get_zone_indicators(db, zid(db, "barrio", "212"))
    for key, (year, value) in PALACIO.items():
        assert (palacio[key].year, palacio[key].value) == (year, pytest.approx(value)), key
    for key, (year, value) in AEROPUERTO.items():
        assert (aeropuerto[key].year, aeropuerto[key].value) == (year, pytest.approx(value)), key
    assert get_zone_indicators(db, zid(db, "distrito", "01"))["renta_neta_hogar"].value != palacio["renta_neta_hogar"].value
    assert get_zone_indicators(db, zid(db, "ciudad", None))                                          # la ciudad también tiene datos


@requires_test_db
def test_no_se_importa_nada_fuera_de_la_lista(db):
    from backend.models.market import ZoneIndicator
    import_panel(db, SAMPLE, "acumulado.csv")
    assert {r.indicator for r in db.query(ZoneIndicator)} == {s.key for s in INDICATORS}


@requires_test_db
def test_el_mismo_archivo_dos_veces_no_hace_nada(db):
    first = import_panel(db, SAMPLE, "acumulado.csv")
    db.commit()
    before = counts(db)
    again = import_panel(db, SAMPLE, "otro_nombre.csv")
    assert (again.status, again.import_id, again.inserted, again.changed) == (ALREADY_IMPORTED, first.import_id, 0, False)
    assert counts(db) == before


@requires_test_db
def test_una_edicion_nueva_solo_agrega_lo_nuevo_o_revisado(db):
    import_panel(db, SAMPLE, "v1.csv")
    before = counts(db)[1]
    newer = SAMPLE_TEXT.rstrip("\r\n") + "\r\n" + "\r\n".join([
        row(2027, "", "11", 2026, PARO, "5,00"),                                      # revisa Palacio 2026 (era 5,41)
        row(2027, "", "11", 2027, PARO, "5,10"),                                      # año nuevo
        row(2027, "", "11", 2025, "Tamaño medio del hogar", "1,94", unit="Media"),    # igual que lo cargado: no se repite
    ]) + "\r\n"
    result = import_panel(db, newer.encode("utf-8"), "v2.csv")
    db.commit()
    assert (result.status, result.inserted, result.revised) == (IMPORTED, 2, 1)
    assert counts(db) == (2, before + 2)
    from backend.models.market import ZoneIndicator
    versions = (db.query(ZoneIndicator).filter_by(zone_id=zid(db, "barrio", "011"), indicator="tasa_paro", year=2026)
                .order_by(ZoneIndicator.import_id).all())
    assert [v.value for v in versions] == [5.41, 5.0]                                  # la versión vieja se conserva
    latest = get_zone_indicators(db, zid(db, "barrio", "011"))["tasa_paro"]
    assert (latest.year, latest.value) == (2027, 5.1)


@requires_test_db
def test_la_revision_de_un_valor_del_ultimo_ano_gana(db):
    import_panel(db, SAMPLE, "v1.csv")
    import_panel(db, (SAMPLE_TEXT + row(2027, "", "11", 2026, PARO, "5,00") + "\r\n").encode("utf-8"), "v2.csv")
    assert get_zone_indicators(db, zid(db, "barrio", "011"))["tasa_paro"].value == 5.0


@requires_test_db
def test_si_un_indicador_desaparece_avisa_y_no_escribe_nada(db):
    content = lines_without(SAMPLE_TEXT, PARO).encode("utf-8")
    with pytest.raises(PanelIndicatorMissing) as exc:
        import_panel(db, content, "sin_paro.csv")
    assert [s.key for s in exc.value.missing] == ["tasa_paro"] and "tasa_paro" in str(exc.value)
    assert counts(db) == (0, 0)


@requires_test_db
def test_si_un_indicador_cambia_de_nombre_sugiere_el_nuevo(db):
    content = rename(SAMPLE_TEXT, PARO, "Tasa absoluta de paro registrado (marzo)").encode("utf-8")
    with pytest.raises(PanelIndicatorMissing) as exc:
        import_panel(db, content, "renombrado.csv")
    assert exc.value.suggestions["tasa_paro"] == ["tasa absoluta de paro registrado marzo"] and "marzo" in str(exc.value)
    assert counts(db) == (0, 0)


@requires_test_db
def test_allow_missing_sigue_con_el_resto_y_deja_constancia(db):
    from backend.models.market import MarketImport, ZoneIndicator
    content = lines_without(SAMPLE_TEXT, PARO).encode("utf-8")
    result = import_panel(db, content, "sin_paro.csv", allow_missing=True)
    assert [s.key for s in result.missing] == ["tasa_paro"] and result.inserted > 0
    assert "tasa_paro" not in {r.indicator for r in db.query(ZoneIndicator)}
    assert json.loads(db.query(MarketImport).one().notes)["faltantes"] == ["tasa_paro"]


@requires_test_db
def test_zonas_sin_sembrar_abortan_sin_escribir(db):
    from backend.models.zone import Zone
    db.query(Zone).filter_by(level="barrio", code="212").update({"code": "999"})
    db.commit()
    with pytest.raises(PanelImportError, match=r"1 zonas .*barrio:212.*seed_zones"):
        import_panel(db, SAMPLE, "acumulado.csv")
    assert counts(db) == (0, 0)


@requires_test_db
def test_un_archivo_sin_valores_no_se_importa(db):
    with pytest.raises(PanelImportError, match="ningún valor"):
        import_panel(db, panel(row(2026, "1", "11", 2026, "Otro indicador", "1,0")), "nada.csv", allow_missing=True)
    assert counts(db) == (0, 0)


@requires_test_db
def test_get_zone_indicators_acepta_id_zona_o_resolvedor_y_filtra(db):
    from backend.models.zone import Zone
    import_panel(db, SAMPLE, "acumulado.csv")
    zone_id = zid(db, "barrio", "011")
    zone = db.get(Zone, zone_id)
    match = ZoneMatch(zone_id=zone_id, level="barrio", matched_on="neighborhood")
    assert get_zone_indicators(db, zone_id) == get_zone_indicators(db, zone) == get_zone_indicators(db, match)
    assert set(get_zone_indicators(db, zone_id, ["tasa_paro", "envejecimiento"])) == {"tasa_paro", "envejecimiento"}
    for nothing in (ZoneMatch(unscorable_reason="no_zone"), None, True, 999_999):
        assert get_zone_indicators(db, nothing) == {}


@requires_test_db
def test_un_barrio_sin_valores_propios_no_hereda_los_del_distrito(db):
    import_panel(db, SAMPLE, "acumulado.csv")
    assert get_zone_indicators(db, zid(db, "barrio", "212"))["renta_neta_hogar"].value == 33236.0
    from backend.models.market import ZoneIndicator
    db.query(ZoneIndicator).filter_by(zone_id=zid(db, "barrio", "212")).delete()
    assert get_zone_indicators(db, zid(db, "barrio", "212")) == {}                              # sin herencia silenciosa


@requires_test_db
def test_restricciones_de_la_base(db):
    from backend.models.market import ZoneIndicator
    result = import_panel(db, SAMPLE, "acumulado.csv")
    db.commit()

    def fails(**overrides):
        data = dict(zone_id=zid(db, "barrio", "011"), indicator="tasa_paro", year=2030, value=1.0, unit="%", import_id=result.import_id)
        data.update(overrides)
        db.add(ZoneIndicator(**data))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    fails(year=1800)
    fails(year=2200)
    fails(year=2026)                                                                   # (zona, indicador, año, carga) repetido
    fails(value=None)
    assert counts(db)[0] == 1


# ── El script ──────────────────────────────────────────────────────────────────

@requires_test_db
def test_script_dry_run_muestra_los_nueve_indicadores_sin_escribir(db, capsys):
    from scripts import import_panel as script
    assert script.main(["--file", str(SAMPLE_PATH)]) == 0
    out = capsys.readouterr().out
    assert out.count("✓") == 9 and "✗" not in out and "[dry-run]" in out
    assert "renta_neta_hogar" in out and "años 2016–2026" in out
    assert counts(db) == (0, 0)


@requires_test_db
def test_script_confirm_escribe_y_la_segunda_vez_no_hace_nada(db, capsys):
    from scripts import import_panel as script
    assert script.main(["--file", str(SAMPLE_PATH), "--confirm"]) == 0
    assert "OK: carga #" in capsys.readouterr().out
    db.expire_all()
    first = counts(db)
    assert first[0] == 1 and first[1] > 100
    assert script.main(["--file", str(SAMPLE_PATH), "--confirm"]) == 0
    assert "ya importado" in capsys.readouterr().out
    db.expire_all()
    assert counts(db) == first


@requires_test_db
def test_script_con_un_indicador_faltante_avisa_sugiere_y_sale_con_error(db, tmp_path, capsys):
    from scripts import import_panel as script
    path = tmp_path / "renombrado.csv"
    path.write_text(rename(SAMPLE_TEXT, PARO, "Tasa absoluta de paro registrado (marzo)"), encoding="utf-8")
    assert script.main(["--file", str(path), "--confirm"]) == script.EXIT_MISSING
    out = capsys.readouterr().out
    assert "ABORTADO" in out and "tasa_paro" in out and "¿será 'tasa absoluta de paro registrado marzo'?" in out and "No se escribió nada" in out
    assert counts(db) == (0, 0)


@requires_test_db
def test_script_allow_missing_sigue_y_avisa(db, tmp_path, capsys):
    from scripts import import_panel as script
    path = tmp_path / "sin_paro.csv"
    path.write_text(lines_without(SAMPLE_TEXT, PARO), encoding="utf-8")
    assert script.main(["--file", str(path), "--allow-missing", "--confirm"]) == 0
    out = capsys.readouterr().out
    assert "✗ tasa_paro" in out and "FALTA" in out and "AVISO: faltan 1 indicadores" in out
    db.expire_all()
    assert counts(db)[0] == 1


@requires_test_db
def test_script_descarga_con_el_cliente_inyectado(db, capsys):
    from scripts import import_panel as script
    http = FakeHttp([meta(RES_CSV), FakeResponse(content=SAMPLE)])
    assert script.main(["--confirm"], http=http) == 0
    assert "origen: https://datos.madrid.es/x/download/acumulado.csv" in capsys.readouterr().out
    db.expire_all()
    from backend.models.market import MarketImport
    assert db.query(MarketImport).one().file_name == "acumulado.csv"


@requires_test_db
def test_script_si_la_descarga_falla_no_escribe(db, capsys):
    from scripts import import_panel as script
    assert script.main(["--confirm"], http=FakeHttp([FakeResponse(503)])) == script.EXIT_FETCH
    assert "No se escribió nada" in capsys.readouterr().out
    assert counts(db) == (0, 0)


@requires_test_db
def test_script_con_archivos_invalidos(db, tmp_path, capsys):
    from scripts import import_panel as script
    bad = tmp_path / "roto.csv"
    bad.write_text("a;b\n1;2\n")
    assert script.main(["--file", str(bad), "--confirm"]) == script.EXIT_BAD_INPUT
    assert "faltan columnas" in capsys.readouterr().out
    assert script.main(["--file", str(tmp_path / "no_existe.csv")]) == script.EXIT_BAD_INPUT
    assert counts(db) == (0, 0)


def test_el_fixture_tiene_la_cabecera_original_del_panel():
    assert SAMPLE_TEXT.splitlines()[0] == HEADER and len(SAMPLE_TEXT.splitlines()) > 250
