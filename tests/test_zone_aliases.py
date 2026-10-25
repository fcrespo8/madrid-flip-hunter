"""data/aliases.csv: formato, contenido y carga como alias 'manual'; cobertura del resolvedor."""
from collections import Counter
from pathlib import Path

import pytest

from backend.market.aliases import AliasFileError, load_aliases_csv, parse_aliases_text, parse_target
from backend.market.coverage import ListingLocation, coverage_report, has_location
from backend.market.registral import load_registral_csv, parse_registral_text
from backend.market.resolver import resolver_from_specs
from backend.market.zones import ZoneSeedError, seed_zones, zone_specs_from_rows
from tests.conftest import requires_test_db

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "precio_registral_barrio.csv"
ALIASES_PATH = ROOT / "data" / "aliases.csv"
needs_data = pytest.mark.skipif(not (CSV_PATH.exists() and ALIASES_PATH.exists()), reason="faltan los datos")

HEADER = "texto,zona,nota\n"
REG_HEADER = "Distrito;Barrio;Año;Precio medio y números índice;Tipo de vivienda;Total\n"
MINI_ROWS = [
    "Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;5.333,59",
    "20. San Blas-Canillejas;20. San Blas-Canillejas;2025;Euros/m2;Usadas;3.900,00",
    "20. San Blas-Canillejas;201. Simancas;2025;Euros/m2;Usadas;4.100,00",
    "02. Arganzuela;02. Arganzuela;2025;Euros/m2;Usadas;5.648,69",
    "02. Arganzuela;026. Palos de la Frontera;2025;Euros/m2;Usadas;5.000,00",
]


@pytest.fixture(scope="module")
def mini_specs():
    return zone_specs_from_rows(parse_registral_text(REG_HEADER + "\n".join(MINI_ROWS) + "\n"))


def parse(body, specs):
    return parse_aliases_text(HEADER + body, specs)


# ── Formato del archivo ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("distrito:20", ("distrito", "20")), ("barrio:026", ("barrio", "026")), ("ciudad", ("ciudad", None)),
    ("out_of_scope", None), (" barrio: 026 ", ("barrio", "026")),
])
def test_parse_target(raw, expected):
    assert parse_target(raw, 2) == expected


@pytest.mark.parametrize("raw", ["", "distrito", "distrito:", "distrito:ab", "barrio:02.6", "zona:20", "Centro", "fuera"])
def test_parse_target_rechaza_lo_que_no_es_un_destino(raw):
    with pytest.raises(AliasFileError):
        parse_target(raw, 2)


def test_parsea_texto_destino_y_nota(mini_specs):
    (a, b, c) = parse('San Blas,distrito:20,"San Blas, a secas"\nPalos de Moguer,barrio:026,Nombre popular\nCoslada,out_of_scope,Otro municipio\n', mini_specs)
    assert (a.text, a.alias_norm, a.target, a.note) == ("San Blas", "san blas", ("distrito", "20"), "San Blas, a secas")
    assert (b.alias_norm, b.target) == ("palos de moguer", ("barrio", "026"))
    assert (c.target, c.note) == (None, "Otro municipio")


@pytest.mark.parametrize("body, message", [
    ("San Blas,distrito:99,x\n", "línea 2: la zona 'distrito:99' no existe"),
    ("San Blas,distrito:20\n", "línea 2: 2 columnas"),
    ("San Blas,distrito:20,\n", "línea 2: falta la nota"),
    (",distrito:20,nota\n", "línea 2: texto vacío"),
    (" - ,distrito:20,nota\n", "línea 2: texto vacío"),
    ("San Blas,distrito:20,a\nsan  BLAS,distrito:20,b\n", "línea 3: 'san  BLAS' repite el alias de la línea 2"),
    ("Simancas,barrio:201,ya es oficial\n", "ya es el nombre oficial"),
    ("San Blas-Canillejas,distrito:20,ya es oficial\n", "ya es el nombre oficial"),
    ("Arganzuela,distrito:20,apunta a otra zona\n", "ya es el nombre oficial"),
])
def test_archivo_mal_formado(mini_specs, body, message):
    with pytest.raises(AliasFileError, match=message):
        parse(body, mini_specs)


def test_cabecera_incorrecta(mini_specs):
    with pytest.raises(AliasFileError, match="cabecera"):
        parse_aliases_text("alias,zona\nSan Blas,distrito:20\n", mini_specs)


def test_lineas_en_blanco_y_bom(mini_specs):
    assert len(parse_aliases_text("﻿" + HEADER + "\nSan Blas,distrito:20,x\n\n", mini_specs)) == 1


# ── El archivo real ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def real():
    if not (CSV_PATH.exists() and ALIASES_PATH.exists()):
        pytest.skip("faltan los datos")
    specs = zone_specs_from_rows(load_registral_csv(CSV_PATH))
    aliases = load_aliases_csv(ALIASES_PATH, specs)
    return specs, aliases, resolver_from_specs(specs, aliases)


@needs_data
def test_el_archivo_real_es_valido_y_tiene_lo_pedido(real):
    _, aliases, _ = real
    by_norm = {a.alias_norm: a for a in aliases}
    assert len(aliases) == len(by_norm) > 100
    assert by_norm["san blas"].target == ("distrito", "20")
    assert by_norm["palos de moguer"].target == ("barrio", "026")
    assert by_norm["san andres"].target == ("barrio", "171")
    assert by_norm["concepcion"].target == ("barrio", "154")
    assert by_norm["fuencarral"].target == ("distrito", "08")
    for town in ("coslada", "alcobendas", "san sebastian de los reyes", "las rozas de madrid", "getafe"):
        assert by_norm[town].target is None, town
    assert all(a.note for a in aliases)


@needs_data
@pytest.mark.parametrize("neighborhood, district, level, code", [
    ("San Blas", "San Blas", "distrito", "20"),
    ("Palos de Moguer", None, "barrio", "026"),
    ("San Andrés", None, "barrio", "171"),
    ("Concepción", None, "barrio", "154"),
    ("Fuencarral", "Fuencarral", "distrito", "08"),
    ("Embajadores - Lavapiés", None, "barrio", "012"),            # el más frecuente de producción
    ("Universidad - Malasaña", None, "barrio", "015"),
    ("Salamanca - La Guindalera", None, "barrio", "044"),          # Remax: 'Distrito - Barrio'
    ("Lista, Salamanca", None, "barrio", "045"),                   # Donpiso: 'Barrio, Distrito'
    ("Moncloa - Aravaca", None, "distrito", "09"),                 # nombre oficial: no necesita alias
    ("Sanchinarro", None, "distrito", "16"),                       # barrio no verificado → distrito
])
def test_alias_reales_resuelven_a_la_zona_esperada(real, neighborhood, district, level, code):
    _, _, resolver = real
    match = resolver.resolve(neighborhood, district)
    assert (match.level, match.code) == (level, code)


@needs_data
@pytest.mark.parametrize("text", ["Coslada", "San Sebastián de los Reyes", "Los Arroyos, San Sebastián de los Reyes", "Alcobendas", "Villaviciona de Odón"])
def test_municipios_de_fuera_son_out_of_scope(real, text):
    _, _, resolver = real
    assert resolver.resolve(text, None).unscorable_reason == "out_of_scope"


@needs_data
def test_una_calle_sigue_sin_resolver_y_no_hay_alias_de_calles(real):
    _, aliases, resolver = real
    assert resolver.resolve("Calle de Ezequiel Solana", None).unscorable_reason == "no_zone"
    assert not [a for a in aliases if a.alias_norm.startswith(("calle ", "avenida ", "plaza ", "paseo ", "redpiso ", "se vende ", "piso en "))]


@needs_data
def test_los_destinos_de_los_alias_existen_y_el_nivel_coincide_con_el_texto(real):
    specs, aliases, _ = real
    keys = {s.key for s in specs}
    assert all(a.target in keys for a in aliases if a.target is not None)
    assert Counter(a.target[0] for a in aliases if a.target is not None) == Counter(
        {"barrio": sum(1 for a in aliases if a.target and a.target[0] == "barrio"),
         "distrito": sum(1 for a in aliases if a.target and a.target[0] == "distrito")})


# ── Cobertura ──────────────────────────────────────────────────────────────────

def test_has_location():
    assert has_location("Centro", None) and has_location(None, "Centro")
    assert not any(has_location(n, d) for n, d in [(None, None), ("", " "), (" - ", None), (None, ",")])


@needs_data
def test_coverage_report_cuenta_zona_fuera_de_alcance_y_sin_resolver(real):
    specs, _, resolver = real
    items = [
        ListingLocation(1, "wallapop", "Embajadores - Lavapiés", None),
        ListingLocation(2, "tecnocasa", "San Blas", "San Blas"),
        ListingLocation(3, "redpiso", "Los Arroyos, San Sebastián de los Reyes", None),
        ListingLocation(4, "redpiso", "Calle de Ezequiel Solana", None),
        ListingLocation(5, "redpiso", "Calle de Ezequiel Solana", None),
        ListingLocation(6, "wallapop", None, None),                       # sin ubicación: no cuenta
    ]
    report = coverage_report(resolver, items)
    assert (report.total, report.with_location, report.matched, report.out_of_scope, report.unresolved) == (6, 5, 2, 1, 2)
    assert report.without_location == 1 and report.by_level == {"barrio": 1, "distrito": 1}
    assert report.zone_coverage == pytest.approx(40.0) and report.recognized_coverage == pytest.approx(60.0)
    assert report.unresolved_texts == {("Calle de Ezequiel Solana", None): 2}
    assert report.unresolved_examples[("Calle de Ezequiel Solana", None)] == (4, "redpiso")
    assert report.by_source["redpiso"] == {"out_of_scope": 1, "unresolved": 2}


@needs_data
def test_los_alias_mejoran_la_cobertura(real):
    specs, aliases, with_aliases = real
    only_official = resolver_from_specs(specs)
    items = [ListingLocation(i, "x", n, d) for i, (n, d) in enumerate([
        ("San Blas", "San Blas"), ("Fuencarral", "Fuencarral"), ("San Andrés", None), ("Palos de Moguer", None),
        ("Concepción", None), ("Coslada", None), ("Chamberí", "Chamberí"), ("Palacio", "Centro")])]
    before, after = coverage_report(only_official, items), coverage_report(with_aliases, items)
    assert (before.matched, after.matched) == (2, 7)                      # Chamberí y Palacio ya resolvían
    assert (before.out_of_scope, after.out_of_scope) == (0, 1)
    assert after.recognized_coverage == 100.0 and after.unresolved == 0


def test_coverage_vacia():
    report = coverage_report(resolver_from_specs([]), [])
    assert (report.total, report.zone_coverage, report.recognized_coverage) == (0, 0.0, 0.0)


# ── El script de cobertura (sin base: se reemplaza la lectura) ─────────────────

@needs_data
def test_script_zone_coverage_muestra_antes_despues_y_la_meta(monkeypatch, capsys):
    from scripts import zone_coverage
    locations = [ListingLocation(i, "wallapop", "Embajadores - Lavapiés", None) for i in range(94)]
    locations += [ListingLocation(1000 + i, "tecnocasa", "Chamberí", "Chamberí") for i in range(4)]
    locations += [ListingLocation(2000 + i, "redpiso", "Calle X", None) for i in range(2)]
    monkeypatch.setattr(zone_coverage, "fetch_locations", lambda include_rejected: locations)
    assert zone_coverage.main(["--top", "5"]) == 0
    out = capsys.readouterr().out
    assert "con ubicación: 100" in out
    assert "ANTES    con zona     4 (  4.0 %)" in out and "DESPUÉS  con zona    98 ( 98.0 %)" in out
    assert "meta 95 % con zona: CUMPLIDA" in out
    assert "barrio='Calle X'" in out and "(ej. id 2000, redpiso)" in out


@needs_data
def test_script_zone_coverage_avisa_si_no_llega_a_la_meta(monkeypatch, capsys):
    from scripts import zone_coverage
    locations = [ListingLocation(i, "redpiso", "Calle X", None) for i in range(10)] + [ListingLocation(99, "x", "Centro", "Centro")]
    monkeypatch.setattr(zone_coverage, "fetch_locations", lambda include_rejected: locations)
    zone_coverage.main([])
    assert "NO cumplida" in capsys.readouterr().out


def test_script_zone_coverage_con_archivo_roto(tmp_path, capsys):
    from scripts import zone_coverage
    bad = tmp_path / "a.csv"
    bad.write_text("texto,zona,nota\nSan Blas,distrito:99,x\n")
    assert zone_coverage.main(["--aliases", str(bad)]) == 2
    assert "ERROR" in capsys.readouterr().out


# ── seed_zones carga los alias como 'manual' ───────────────────────────────────

@pytest.fixture
def clean():
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias

    def wipe():
        db = SessionLocal()
        db.query(ZoneAlias).delete()
        db.query(Zone).filter(Zone.parent_id.is_not(None)).delete()
        db.query(Zone).delete()
        db.commit()
        db.close()
    wipe()
    yield
    wipe()


def _seed(specs, aliases):
    from backend.models.database import SessionLocal
    db = SessionLocal()
    try:
        result = seed_zones(db, specs, aliases)
        db.commit()
        return result
    finally:
        db.close()


def _aliases_in_db():
    from backend.models.database import SessionLocal
    from backend.models.zone import ZoneAlias
    db = SessionLocal()
    try:
        return {a.alias_norm: (a.kind, a.source, a.note, a.zone_id) for a in db.query(ZoneAlias)}
    finally:
        db.close()


@requires_test_db
def test_seed_carga_los_alias_manuales(clean, mini_specs):
    aliases = parse('San Blas,distrito:20,Los portales\nPalos de Moguer,barrio:026,Nombre popular\nCoslada,out_of_scope,Otro municipio\n', mini_specs)
    result = _seed(mini_specs, aliases)
    assert (result.zones_created, result.aliases_created, result.manual_created) == (5, 5, 3)
    db_aliases = _aliases_in_db()
    assert db_aliases["san blas"][:3] == ("distrito", "manual", "Los portales")
    assert db_aliases["palos de moguer"][:3] == ("barrio", "manual", "Nombre popular")
    assert db_aliases["coslada"] == ("out_of_scope", "manual", "Otro municipio", None)
    assert Counter(v[1] for v in db_aliases.values()) == {"official": 5, "manual": 3}


@requires_test_db
def test_seed_con_alias_es_idempotente(clean, mini_specs):
    aliases = parse('San Blas,distrito:20,Los portales\nCoslada,out_of_scope,Otro municipio\n', mini_specs)
    _seed(mini_specs, aliases)
    before = _aliases_in_db()
    again = _seed(mini_specs, aliases)
    assert (again.manual_created, again.manual_updated, again.aliases_created) == (0, 0, 0) and not again.changed
    assert _aliases_in_db() == before


@requires_test_db
def test_seed_actualiza_un_alias_manual_si_cambia_el_archivo(clean, mini_specs):
    _seed(mini_specs, parse("San Blas,distrito:20,Primera nota\n", mini_specs))
    result = _seed(mini_specs, parse("San Blas,barrio:201,Ahora es Simancas\n", mini_specs))
    assert (result.manual_updated, result.manual_created) == (1, 0)
    kind, source, note, _ = _aliases_in_db()["san blas"]
    assert (kind, source, note) == ("barrio", "manual", "Ahora es Simancas")


@requires_test_db
def test_seed_no_borra_un_alias_manual_que_salio_del_archivo(clean, mini_specs):
    _seed(mini_specs, parse("San Blas,distrito:20,x\nCoslada,out_of_scope,y\n", mini_specs))
    result = _seed(mini_specs, parse("San Blas,distrito:20,x\n", mini_specs))
    assert result.manual_not_in_source == 1 and not result.changed
    assert "coslada" in _aliases_in_db()


@requires_test_db
def test_seed_no_pisa_un_alias_oficial(clean, mini_specs):
    from backend.market.aliases import AliasSpec
    _seed(mini_specs, [])
    clash = AliasSpec("Simancas", "simancas", ("distrito", "20"), "intento de pisar el oficial")   # salteando la validación del archivo
    with pytest.raises(ZoneSeedError, match="choca con un alias oficial"):
        _seed(mini_specs, [clash])
    assert _aliases_in_db()["simancas"][1] == "official"


@requires_test_db
def test_seed_con_un_destino_que_no_se_sembro(clean, mini_specs):
    from backend.market.aliases import AliasSpec
    with pytest.raises(ZoneSeedError, match="no está entre las zonas sembradas"):
        _seed(mini_specs, [AliasSpec("X", "x", ("barrio", "999"), "n")])


@requires_test_db
@needs_data
def test_seed_real_con_todos_los_alias(clean, real):
    specs, aliases, _ = real
    result = _seed(specs, aliases)
    assert (result.zones_created, result.aliases_created, result.manual_created) == (153, 153, len(aliases))
    assert Counter(v[1] for v in _aliases_in_db().values()) == {"official": 153, "manual": len(aliases)}
    assert not _seed(specs, aliases).changed
