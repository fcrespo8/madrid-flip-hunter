"""market_imports / market_prices: importador del CSV registral y get_reference_price."""
import ast
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from backend.market.importer import (
    ALREADY_IMPORTED, IMPORTED, MarketImportError, file_sha256, import_registral, import_registral_file, period_end,
    row_zone_key,
)
from backend.market.registral import load_registral_csv, parse_registral_text
from backend.market.reference import REASON_NO_DATA, REASON_STALE, get_reference_price
from backend.market.resolver import ZoneMatch
from backend.market.zones import seed_zones, zone_specs_from_rows
from tests.conftest import requires_test_db

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "precio_registral_barrio.csv"
needs_csv = pytest.mark.skipif(not CSV_PATH.exists(), reason="data/raw/precio_registral_barrio.csv no está")

HEADER = "Distrito;Barrio;Año;Precio medio y números índice;Tipo de vivienda;Total\n"


def csv_bytes(lines, housing="Usadas") -> bytes:
    body = "".join(f"{line};Euros/m2;{housing};{price}\n" for line, price in lines)
    return (HEADER + body).encode("utf-8")


# Ciudad; Centro > Palacio (con dato) y Embajadores (nunca); Arganzuela > Palos (solo 2024) y Atocha (nunca).
def mini_lines(centro_2025="7.171,36", palacio_2025="6.954,58"):
    return [
        ("Ciudad de Madrid;Ciudad de Madrid;2024", "4.544,27"), ("Ciudad de Madrid;Ciudad de Madrid;2025", "5.333,59"),
        ("01. Centro;01. Centro;2024", "6.279,87"), ("01. Centro;01. Centro;2025", centro_2025),
        ("01. Centro;011. Palacio;2024", "6.355,20"), ("01. Centro;011. Palacio;2025", palacio_2025),
        ("01. Centro;012. Embajadores;2024", ".."), ("01. Centro;012. Embajadores;2025", ".."),
        ("02. Arganzuela;02. Arganzuela;2024", "5.000,00"), ("02. Arganzuela;02. Arganzuela;2025", "5.648,69"),
        ("02. Arganzuela;026. Palos de la Frontera;2024", "5.100,00"), ("02. Arganzuela;026. Palos de la Frontera;2025", "0,00"),
        ("02. Arganzuela;027. Atocha;2024", "0,00"), ("02. Arganzuela;027. Atocha;2025", "0,00"),
    ]


MINI = csv_bytes(mini_lines())


def specs_of(content: bytes):
    return zone_specs_from_rows(parse_registral_text(content.decode("utf-8")))


# ── Piezas puras ───────────────────────────────────────────────────────────────

def test_file_sha256_y_period_end():
    assert file_sha256(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert period_end(2025) == date(2025, 12, 31)


def test_row_zone_key():
    rows = {(r.level, r.barrio_name or r.district_name): r for r in parse_registral_text(MINI.decode())}
    assert row_zone_key(rows[("ciudad", None)]) == ("ciudad", None)
    assert row_zone_key(rows[("distrito", "Centro")]) == ("distrito", "01")
    assert row_zone_key(rows[("barrio", "Palacio")]) == ("barrio", "011")


# ── Modelo y migración ─────────────────────────────────────────────────────────

def _migration_tree():
    path = next((ROOT / "alembic" / "versions").glob("*_add_market_imports_and_prices.py"))
    return ast.parse(path.read_text())


def test_modelos():
    from backend.models.market import MarketImport, MarketPrice
    assert set(MarketImport.__table__.c.keys()) == {"id", "source", "file_name", "sha256", "period_label", "loaded_at",
                                                    "row_count", "skipped_count", "notes"}
    assert set(MarketPrice.__table__.c.keys()) == {"id", "zone_id", "source", "period", "price_m2", "import_id"}
    assert not any(c.nullable for c in MarketPrice.__table__.c)


def test_la_migracion_crea_las_mismas_columnas_que_los_modelos():
    from backend.models.market import MarketImport, MarketPrice
    created = {}
    for node in ast.walk(_migration_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_table":
            created[node.args[0].value] = {c.args[0].value for c in node.args[1:]
                                           if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "Column"}
    assert created == {"market_imports": set(MarketImport.__table__.c.keys()),
                       "market_prices": set(MarketPrice.__table__.c.keys())}


def test_la_migracion_se_deshace_en_orden_y_es_la_cabeza():
    down = next(f for f in _migration_tree().body if isinstance(f, ast.FunctionDef) and f.name == "downgrade")
    dropped = [n.args[0].value for n in ast.walk(down) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "drop_table"]
    assert dropped == ["market_prices", "market_imports"]                         # los precios dependen de la carga

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert script.get_revision("a8b3d6f1e2c7").down_revision == "f7a2c4e8d9b3"


# ── Contra la base de prueba ───────────────────────────────────────────────────

@pytest.fixture
def db():
    from backend.models.database import SessionLocal
    from backend.models.market import MarketImport, MarketPrice
    from backend.models.zone import Zone, ZoneAlias

    def wipe(session):
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


def seeded(db, content=MINI):
    seed_zones(db, specs_of(content))
    db.commit()


def zone_id(db, level, code):
    from backend.models.zone import Zone
    return db.query(Zone).filter_by(level=level, code=code).one().id


def counts(db):
    from backend.models.market import MarketImport, MarketPrice
    return db.query(MarketImport).count(), db.query(MarketPrice).count()


@requires_test_db
def test_la_primera_carga_registra_la_importacion_y_los_precios(db):
    from backend.models.market import MarketImport, MarketPrice
    seeded(db)
    result = import_registral(db, MINI, "mini.csv")
    db.commit()

    assert (result.status, result.rows_read, result.inserted, result.skipped_no_data) == (IMPORTED, 14, 9, 5)
    assert (result.period_from, result.period_to, result.zones_with_data) == (date(2024, 12, 31), date(2025, 12, 31), 5)
    assert counts(db) == (1, 9)
    record = db.query(MarketImport).one()
    assert (record.source, record.file_name, record.sha256, record.period_label) == ("registral_usadas", "mini.csv", file_sha256(MINI), "2024-2025")
    assert (record.row_count, record.skipped_count) == (9, 5) and record.loaded_at is not None
    assert {p.import_id for p in db.query(MarketPrice)} == {record.id}


@requires_test_db
def test_los_barrios_sin_dato_no_se_insertan(db):
    from backend.models.market import MarketPrice
    seeded(db)
    import_registral(db, MINI, "mini.csv")
    for code in ("012", "027"):                                                  # Embajadores y Atocha: nunca hubo dato
        assert db.query(MarketPrice).filter_by(zone_id=zone_id(db, "barrio", code)).count() == 0
    palos = db.query(MarketPrice).filter_by(zone_id=zone_id(db, "barrio", "026")).all()
    assert [(p.period, p.price_m2) for p in palos] == [(date(2024, 12, 31), 5100.0)]        # 2025 (0,00) no
    assert db.query(MarketPrice).filter(MarketPrice.price_m2 <= 0).count() == 0


@requires_test_db
def test_el_mismo_archivo_dos_veces_no_hace_nada(db):
    seeded(db)
    first = import_registral(db, MINI, "mini.csv")
    db.commit()
    again = import_registral(db, MINI, "mini.csv")
    db.commit()
    assert (again.status, again.import_id, again.inserted, again.changed) == (ALREADY_IMPORTED, first.import_id, 0, False)
    assert counts(db) == (1, 9)


@requires_test_db
def test_el_mismo_contenido_con_otro_nombre_tambien_es_la_misma_carga(db):
    seeded(db)
    import_registral(db, MINI, "mini.csv")
    assert import_registral(db, MINI, "copia_renombrada.csv").status == ALREADY_IMPORTED
    assert counts(db) == (1, 9)


@requires_test_db
def test_un_archivo_distinto_agrega_filas_y_no_pisa_las_anteriores(db):
    from backend.models.market import MarketPrice
    seeded(db)
    import_registral(db, MINI, "v1.csv")
    revised = csv_bytes(mini_lines(centro_2025="7.300,00"))
    result = import_registral(db, revised, "v2.csv")
    db.commit()

    assert result.status == IMPORTED and counts(db) == (2, 18)
    centro = (db.query(MarketPrice).filter_by(zone_id=zone_id(db, "distrito", "01"), period=date(2025, 12, 31))
              .order_by(MarketPrice.import_id).all())
    assert [p.price_m2 for p in centro] == [7171.36, 7300.0]                              # las dos versiones siguen ahí
    assert len({p.import_id for p in centro}) == 2


@requires_test_db
def test_el_archivo_viejo_no_se_vuelve_a_cargar_despues_de_uno_nuevo(db):
    seeded(db)
    import_registral(db, MINI, "v1.csv")
    import_registral(db, csv_bytes(mini_lines(centro_2025="7.300,00")), "v2.csv")
    assert import_registral(db, MINI, "v1.csv").status == ALREADY_IMPORTED
    assert counts(db) == (2, 18)


@requires_test_db
def test_zonas_sin_sembrar_abortan_sin_escribir_nada(db):
    with pytest.raises(MarketImportError, match="no están en la base"):
        import_registral(db, MINI, "mini.csv")
    assert counts(db) == (0, 0)


@requires_test_db
def test_zonas_sembradas_a_medias_listan_las_que_faltan(db):
    from backend.models.zone import Zone
    seeded(db)
    db.query(Zone).filter_by(level="barrio", code="011").update({"code": "999"})
    db.commit()
    with pytest.raises(MarketImportError, match=r"1 zonas .*barrio:011"):
        import_registral(db, MINI, "mini.csv")
    assert counts(db) == (0, 0)


@requires_test_db
def test_otro_tipo_de_vivienda_no_se_importa_como_usadas(db):
    seeded(db)
    with pytest.raises(MarketImportError, match="vivienda usada"):
        import_registral(db, csv_bytes(mini_lines(), housing="Nuevas"), "nuevas.csv")
    assert counts(db) == (0, 0)


@requires_test_db
def test_un_archivo_roto_no_escribe_nada(db):
    from backend.market.registral import RegistralParseError
    seeded(db)
    with pytest.raises(RegistralParseError):
        import_registral(db, b"esto;no;es;el;archivo\n", "roto.csv")
    assert counts(db) == (0, 0)


@requires_test_db
def test_restricciones_de_la_base(db):
    from backend.models.market import MarketImport, MarketPrice
    seeded(db)
    import_registral(db, MINI, "mini.csv")
    db.commit()
    record = db.query(MarketImport).one()
    zid = zone_id(db, "distrito", "01")

    def fails(obj):
        db.add(obj)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    fails(MarketPrice(zone_id=zid, source="registral_usadas", period=date(2030, 12, 31), price_m2=0.0, import_id=record.id))
    fails(MarketPrice(zone_id=zid, source="registral_usadas", period=date(2030, 12, 31), price_m2=-5.0, import_id=record.id))
    fails(MarketPrice(zone_id=zid, source="registral_usadas", period=date(2025, 12, 31), price_m2=1.0, import_id=record.id))   # repetido
    fails(MarketImport(source="registral_usadas", file_name="x", sha256=record.sha256, period_label="x", row_count=0, skipped_count=0))
    fails(MarketImport(source="otra", file_name="x", sha256="abc", period_label="x", row_count=0, skipped_count=0))               # largo incorrecto
    assert counts(db) == (1, 9)


# ── get_reference_price ────────────────────────────────────────────────────────

@pytest.fixture
def loaded(db):
    seeded(db)
    import_registral(db, MINI, "mini.csv")
    db.commit()
    return db


@requires_test_db
def test_un_barrio_con_dato_usa_su_propio_precio(loaded):
    ref = get_reference_price(loaded, zone_id(loaded, "barrio", "011"))
    assert (ref.price_m2, ref.level, ref.period, ref.is_fallback, ref.fallback_reason) == (6954.58, "barrio", date(2025, 12, 31), False, None)
    assert ref.zone_id == zone_id(loaded, "barrio", "011")


@requires_test_db
def test_un_barrio_sin_dato_cae_al_distrito(loaded):
    for code in ("012", "027"):                                                  # Embajadores y Atocha
        ref = get_reference_price(loaded, zone_id(loaded, "barrio", code))
        assert ref.level == "distrito" and ref.is_fallback and ref.fallback_reason == REASON_NO_DATA
    embajadores = get_reference_price(loaded, zone_id(loaded, "barrio", "012"))
    assert (embajadores.price_m2, embajadores.zone_id, embajadores.period) == (7171.36, zone_id(loaded, "distrito", "01"), date(2025, 12, 31))


@requires_test_db
def test_un_barrio_con_precio_viejo_usa_el_del_distrito(loaded):
    """Palos de la Frontera solo tiene 2024 y su distrito ya tiene 2025: el dato disponible más reciente es el del distrito."""
    ref = get_reference_price(loaded, zone_id(loaded, "barrio", "026"))
    assert (ref.level, ref.price_m2, ref.period, ref.fallback_reason) == ("distrito", 5648.69, date(2025, 12, 31), REASON_STALE)


@requires_test_db
def test_distritos_y_ciudad_usan_el_suyo(loaded):
    district = get_reference_price(loaded, zone_id(loaded, "distrito", "02"))
    city = get_reference_price(loaded, zone_id(loaded, "ciudad", None))
    assert (district.level, district.price_m2, district.is_fallback) == ("distrito", 5648.69, False)
    assert (city.level, city.price_m2, city.is_fallback) == ("ciudad", 5333.59, False)


@requires_test_db
def test_acepta_un_id_una_zona_o_el_resultado_del_resolvedor(loaded):
    from backend.models.zone import Zone
    zid = zone_id(loaded, "barrio", "011")
    zone = loaded.get(Zone, zid)
    match = ZoneMatch(zone_id=zid, level="barrio", matched_on="neighborhood")
    assert get_reference_price(loaded, zid) == get_reference_price(loaded, zone) == get_reference_price(loaded, match)


@requires_test_db
def test_sin_zona_o_sin_precio_devuelve_none(loaded):
    assert get_reference_price(loaded, ZoneMatch(unscorable_reason="no_zone")) is None
    assert get_reference_price(loaded, ZoneMatch(unscorable_reason="out_of_scope")) is None
    assert get_reference_price(loaded, 999_999) is None
    assert get_reference_price(loaded, None) is None
    assert get_reference_price(loaded, True) is None                              # un bool no es un id
    assert get_reference_price(loaded, zone_id(loaded, "barrio", "011"), source="otra_fuente") is None


@requires_test_db
def test_si_el_distrito_no_tiene_precio_el_barrio_usa_el_suyo(db):
    districts = ("02. Arganzuela;02. Arganzuela", "01. Centro;01. Centro")
    lines = [(l, ".." if l.startswith(districts) else price) for l, price in mini_lines()]     # las zonas existen, sin precio de distrito
    content = csv_bytes(lines)
    seeded(db, content)
    import_registral(db, content, "sin_distritos.csv")
    assert get_reference_price(db, zone_id(db, "barrio", "011")).level == "barrio"
    assert get_reference_price(db, zone_id(db, "barrio", "012")) is None           # sin dato y sin distrito: nada
    assert get_reference_price(db, zone_id(db, "distrito", "01")) is None


@requires_test_db
def test_la_carga_mas_reciente_manda_para_el_mismo_periodo(db):
    seeded(db)
    import_registral(db, MINI, "v1.csv")
    import_registral(db, csv_bytes(mini_lines(centro_2025="7.300,00")), "v2.csv")
    ref = get_reference_price(db, zone_id(db, "distrito", "01"))
    assert (ref.price_m2, ref.period) == (7300.0, date(2025, 12, 31))


@requires_test_db
def test_el_periodo_mas_nuevo_gana_aunque_venga_de_una_carga_anterior(db):
    seeded(db)
    import_registral(db, csv_bytes(mini_lines()), "con_2025.csv")
    only_2024 = csv_bytes([l for l in mini_lines() if ";2024" in l[0]])
    import_registral(db, only_2024, "solo_2024_posterior.csv")                    # carga posterior, pero de un período viejo
    ref = get_reference_price(db, zone_id(db, "distrito", "01"))
    assert (ref.price_m2, ref.period) == (7171.36, date(2025, 12, 31))


@requires_test_db
def test_un_ano_nuevo_se_suma_y_pasa_a_ser_la_referencia(db):
    seeded(db)
    import_registral(db, MINI, "hasta_2025.csv")
    with_2026 = csv_bytes(mini_lines() + [("01. Centro;01. Centro;2026", "7.900,00"), ("01. Centro;011. Palacio;2026", "7.500,00")])
    import_registral(db, with_2026, "hasta_2026.csv")
    palacio = get_reference_price(db, zone_id(db, "barrio", "011"))
    assert (palacio.price_m2, palacio.period, palacio.level) == (7500.0, date(2026, 12, 31), "barrio")
    assert get_reference_price(db, zone_id(db, "distrito", "01")).price_m2 == 7900.0


# ── Con el archivo real ────────────────────────────────────────────────────────

@pytest.fixture
def real_loaded(db):
    seed_zones(db, zone_specs_from_rows(load_registral_csv(CSV_PATH)))
    db.commit()
    return db


@requires_test_db
@needs_csv
def test_el_archivo_real(real_loaded):
    db = real_loaded
    result = import_registral_file(db, CSV_PATH)
    db.commit()
    assert (result.rows_read, result.inserted, result.skipped_no_data) == (2907, 1676, 1231)
    assert (result.period_from, result.period_to, result.zones_with_data) == (date(2007, 12, 31), date(2025, 12, 31), 151)
    assert counts(db) == (1, 1676)
    assert import_registral_file(db, CSV_PATH).status == ALREADY_IMPORTED
    assert counts(db) == (1, 1676)


@requires_test_db
@needs_csv
def test_precios_de_referencia_reales(real_loaded):
    from backend.models.zone import Zone
    db = real_loaded
    import_registral_file(db, CSV_PATH)
    db.commit()
    palacio = get_reference_price(db, zone_id(db, "barrio", "011"))
    aeropuerto = get_reference_price(db, zone_id(db, "barrio", "212"))
    atocha = get_reference_price(db, zone_id(db, "barrio", "027"))
    assert (palacio.price_m2, palacio.level) == (6954.58, "barrio")
    assert (aeropuerto.price_m2, aeropuerto.level, aeropuerto.zone_id, aeropuerto.fallback_reason) == \
           (4413.92, "distrito", zone_id(db, "distrito", "21"), REASON_NO_DATA)
    assert (atocha.price_m2, atocha.level) == (5648.69, "distrito")
    assert get_reference_price(db, zone_id(db, "ciudad", None)).price_m2 == 5333.59
    # Los 131 barrios tienen referencia: 129 con precio propio y 2 (Atocha y Aeropuerto) con el de su distrito.
    barrios = [get_reference_price(db, zid) for (zid,) in db.query(Zone.id).filter_by(level="barrio")]
    assert len(barrios) == 131 and all(b is not None for b in barrios)
    assert sum(1 for b in barrios if b.is_fallback) == 2


# ── El script ──────────────────────────────────────────────────────────────────

@requires_test_db
def test_script_dry_run_no_escribe(db, tmp_path, capsys):
    from scripts import import_registral as script
    seeded(db)
    path = tmp_path / "mini.csv"
    path.write_bytes(MINI)
    assert script.main(["--csv", str(path)]) == 0
    out = capsys.readouterr().out
    assert "a insertar: 9 (en 5 zonas)" in out and "sin dato, no se insertan: 5" in out and "[dry-run]" in out
    assert counts(db) == (0, 0)


@requires_test_db
def test_script_confirm_escribe_y_la_segunda_vez_no_hace_nada(db, tmp_path, capsys):
    from scripts import import_registral as script
    seeded(db)
    path = tmp_path / "mini.csv"
    path.write_bytes(MINI)
    assert script.main(["--csv", str(path), "--confirm"]) == 0
    assert "OK: carga #" in capsys.readouterr().out
    db.expire_all()
    assert counts(db) == (1, 9)
    assert script.main(["--csv", str(path), "--confirm"]) == 0
    assert "ya importado" in capsys.readouterr().out
    db.expire_all()
    assert counts(db) == (1, 9)


@requires_test_db
def test_script_con_zonas_sin_sembrar_o_archivo_roto_sale_con_error(db, tmp_path, capsys):
    from scripts import import_registral as script
    path = tmp_path / "mini.csv"
    path.write_bytes(MINI)
    assert script.main(["--csv", str(path), "--confirm"]) == script.EXIT_BAD_INPUT
    assert "Sembralas primero" in capsys.readouterr().out
    bad = tmp_path / "roto.csv"
    bad.write_text("nada;que;ver\n")
    assert script.main(["--csv", str(bad), "--confirm"]) == script.EXIT_BAD_INPUT
    assert script.main(["--csv", str(tmp_path / "no_existe.csv")]) == script.EXIT_BAD_INPUT
    assert counts(db) == (0, 0)
