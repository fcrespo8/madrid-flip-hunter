"""resolve(neighborhood, district) contra zones y zone_aliases: barrio primero, luego distrito, sin subcadenas."""
from pathlib import Path

import pytest

from backend.market.registral import load_registral_csv
from backend.market.resolver import (
    DETAIL_NO_ALIAS, DETAIL_NO_LOCATION, AliasInfo, ZoneInfo, ZoneMatch, ZoneResolver, load_resolver, resolve,
)
from backend.market.zones import normalize_zone_name, seed_zones, zone_specs_from_rows
from tests.conftest import requires_test_db

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "precio_registral_barrio.csv"
needs_csv = pytest.mark.skipif(not CSV_PATH.exists(), reason="data/raw/precio_registral_barrio.csv no está")

# Alias que hoy no existen en la base (llegan con data/aliases.csv, paso 5): los tests los ponen a mano.
MANUAL_ALIASES = [
    ("san blas", "distrito", "20"),                 # los portales dicen "San Blas"; el distrito oficial es San Blas-Canillejas
    ("palos de moguer", "barrio", "026"),           # oficial: Palos de la Frontera
]
OUT_OF_SCOPE = ["coslada", "san sebastian de los reyes", "alcobendas"]


def build_resolver() -> tuple[ZoneResolver, dict]:
    """Resolvedor en memoria con las 153 zonas oficiales, sus alias oficiales y los manuales de arriba."""
    specs = zone_specs_from_rows(load_registral_csv(CSV_PATH))
    zones = [ZoneInfo(i, s.level, s.code, s.name, None) for i, s in enumerate(specs, start=1)]
    by_key = {(z.level, z.code): z for z in zones}
    zones = [ZoneInfo(z.id, z.level, z.code, z.name, by_key[s.parent].id if s.parent else None)
             for z, s in zip(zones, specs)]
    by_key = {(z.level, z.code): z for z in zones}
    aliases = [AliasInfo(s.name_norm, by_key[s.key].id, s.level) for s in specs]
    aliases += [AliasInfo(text, by_key[(level, code)].id, level) for text, level, code in MANUAL_ALIASES]
    aliases += [AliasInfo(text, None, "out_of_scope") for text in OUT_OF_SCOPE]
    return ZoneResolver(zones, aliases), by_key


@pytest.fixture(scope="module")
def resolver():
    if not CSV_PATH.exists():
        pytest.skip("falta el CSV")
    return build_resolver()


# ── Los casos pedidos ──────────────────────────────────────────────────────────

@needs_csv
@pytest.mark.parametrize("neighborhood, district", [
    ("Moncloa - Aravaca", None),
    ("Moncloa-Aravaca", "Moncloa-Aravaca"),
    ("moncloa aravaca", None),
    ("MONCLOA – ARAVACA", None),
])
def test_moncloa_aravaca_es_el_distrito_oficial(resolver, neighborhood, district):
    r, zones = resolver
    match = r.resolve(neighborhood, district)
    assert (match.zone_id, match.level, match.code, match.name) == (zones[("distrito", "09")].id, "distrito", "09", "Moncloa-Aravaca")
    assert match.matched and match.unscorable_reason is None and match.matched_on == "neighborhood"


@needs_csv
def test_calle_de_ezequiel_solana_no_matchea_ni_con_sol(resolver):
    r, _ = resolver
    match = r.resolve("Calle de Ezequiel Solana", None)
    assert not match.matched
    assert (match.unscorable_reason, match.detail, match.zone_id) == ("no_zone", DETAIL_NO_ALIAS, None)


@needs_csv
def test_calle_de_ezequiel_solana_con_distrito_cae_al_distrito(resolver):
    r, zones = resolver
    match = r.resolve("Calle de Ezequiel Solana", "Centro")
    assert (match.zone_id, match.level, match.matched_on) == (zones[("distrito", "01")].id, "distrito", "district")


@needs_csv
@pytest.mark.parametrize("neighborhood, district", [("San Blas", None), ("San Blas", "San Blas"), (None, "San Blas"), ("san blas", "SAN BLAS")])
def test_san_blas_resuelve_al_distrito_san_blas_canillejas(resolver, neighborhood, district):
    r, zones = resolver
    match = r.resolve(neighborhood, district)
    assert (match.zone_id, match.name, match.level) == (zones[("distrito", "20")].id, "San Blas-Canillejas", "distrito")


@needs_csv
def test_san_blas_sin_su_alias_no_resuelve():
    """Documenta por qué hace falta el alias manual: el nombre oficial normalizado es 'san blas canillejas'."""
    r, _ = build_resolver()
    bare = ZoneResolver(r._zones.values(), [a for a in r._aliases.values() if a.alias_norm != "san blas"])
    assert bare.resolve("San Blas", "San Blas").unscorable_reason == "no_zone"


@needs_csv
@pytest.mark.parametrize("neighborhood, district", [
    ("Coslada", None),
    ("Coslada", "Centro"),                                    # el municipio de fuera manda sobre el distrito
    ("San Sebastián de los Reyes", None),
    ("Alcobendas", "Alcobendas"),
    (None, "Coslada"),
])
def test_un_municipio_de_fuera_es_out_of_scope(resolver, neighborhood, district):
    r, _ = resolver
    match = r.resolve(neighborhood, district)
    assert (match.matched, match.unscorable_reason, match.zone_id, match.level) == (False, "out_of_scope", None, None)


# ── Orden: barrio primero, después distrito ────────────────────────────────────

@needs_csv
def test_el_barrio_manda_sobre_el_distrito(resolver):
    r, zones = resolver
    match = r.resolve("Palacio", "Chamberí")                  # contradictorio: gana el barrio
    assert (match.zone_id, match.level, match.matched_on) == (zones[("barrio", "011")].id, "barrio", "neighborhood")
    assert match.parent_id == zones[("distrito", "01")].id


@needs_csv
def test_barrio_desconocido_cae_al_distrito(resolver):
    r, zones = resolver
    match = r.resolve("Atlantis", "Retiro")
    assert (match.zone_id, match.level, match.matched_on, match.alias) == (zones[("distrito", "03")].id, "distrito", "district", "retiro")


@needs_csv
def test_un_barrio_escrito_en_el_campo_distrito_tambien_resuelve(resolver):
    r, zones = resolver
    match = r.resolve(None, "Palacio")
    assert (match.zone_id, match.level, match.matched_on) == (zones[("barrio", "011")].id, "barrio", "district")


@needs_csv
def test_alias_manual_de_barrio(resolver):
    r, zones = resolver
    assert r.resolve("Palos de Moguer", None).zone_id == zones[("barrio", "026")].id


@needs_csv
def test_la_ciudad_resuelve_a_nivel_ciudad(resolver):
    r, zones = resolver
    match = r.resolve("Ciudad de Madrid", None)
    assert (match.level, match.zone_id) == ("ciudad", zones[("ciudad", None)].id)


# ── Sin subcadenas, sin ubicación ──────────────────────────────────────────────

@needs_csv
@pytest.mark.parametrize("text", [
    "Calle de Ezequiel Solana",       # 'sol' dentro de 'solana'
    "Los Pilares",                    # 'pilar'
    "Listado de pisos",               # 'lista'
    "Palacios",                       # 'palacio' + s
    "Centro Comercial Xanadú",        # 'centro' como palabra suelta, pero no es el alias completo
    "Plaza del Sol",                  # 'sol' como palabra entera dentro de una frase: tampoco
    "Bellas Vistas, Tetuan",          # compuesto: necesita alias explícito
    "Moncloa",                        # no es una zona oficial (el distrito es Moncloa-Aravaca)
    "Madrid",
])
def test_solo_matchea_el_texto_completo(resolver, text):
    r, _ = resolver
    assert r.resolve(text, None).unscorable_reason == "no_zone"


@needs_csv
@pytest.mark.parametrize("neighborhood, district", [(None, None), ("", ""), ("   ", None), (None, " - "), (" , ", "")])
def test_sin_ubicacion_es_no_zone_con_detalle(resolver, neighborhood, district):
    r, _ = resolver
    match = r.resolve(neighborhood, district)
    assert (match.matched, match.unscorable_reason, match.detail) == (False, "no_zone", DETAIL_NO_LOCATION)


@needs_csv
def test_tildes_mayusculas_y_espacios(resolver):
    r, zones = resolver
    for text in ("CHAMBERÍ", "chamberi", "  Chamberí ", "Chamberi"):
        assert r.resolve(text, None).zone_id == zones[("distrito", "07")].id


@needs_csv
def test_entradas_que_no_son_texto_no_rompen(resolver):
    r, _ = resolver
    assert r.resolve(123, None).detail == DETAIL_NO_LOCATION          # type: ignore[arg-type]
    assert r.resolve(None, ["Centro"]).unscorable_reason == "no_zone"  # type: ignore[arg-type]


@needs_csv
def test_cada_zona_oficial_se_resuelve_a_si_misma(resolver):
    r, zones = resolver
    assert len(zones) == 153
    for (level, code), zone in zones.items():
        match = r.resolve(zone.name, None)
        assert (match.zone_id, match.level) == (zone.id, level), zone.name
        assert r.resolve(None, zone.name).zone_id == zone.id


def test_un_alias_huerfano_se_ignora():
    r = ZoneResolver([ZoneInfo(1, "distrito", "01", "Centro", None)],
                     [AliasInfo("centro", 99, "distrito"), AliasInfo("centro historico", 1, "distrito")])
    assert r.resolve("Centro", None).unscorable_reason == "no_zone"
    assert r.resolve("Centro", "Centro Histórico").zone_id == 1


def test_zone_match_vacio_no_esta_resuelto():
    assert not ZoneMatch().matched


# ── Contra la base de prueba ───────────────────────────────────────────────────

@pytest.fixture
def seeded_db():
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias

    def wipe(db):
        db.query(ZoneAlias).delete()
        db.query(Zone).filter(Zone.parent_id.is_not(None)).delete()
        db.query(Zone).delete()
        db.commit()

    db = SessionLocal()
    wipe(db)
    seed_zones(db, zone_specs_from_rows(load_registral_csv(CSV_PATH)))
    zones = {(z.level, z.code): z for z in db.query(Zone)}
    for text, level, code in MANUAL_ALIASES:
        db.add(ZoneAlias(alias_norm=text, zone_id=zones[(level, code)].id, kind=level, source="manual"))
    for text in OUT_OF_SCOPE:
        db.add(ZoneAlias(alias_norm=text, zone_id=None, kind="out_of_scope", source="manual"))
    db.commit()
    yield db
    wipe(db)
    db.close()


@requires_test_db
@needs_csv
def test_load_resolver_desde_la_base(seeded_db):
    from backend.models.zone import Zone
    r = load_resolver(seeded_db)
    moncloa = seeded_db.query(Zone).filter_by(level="distrito", code="09").one()
    assert r.resolve("Moncloa - Aravaca", None).zone_id == moncloa.id
    assert r.resolve("Calle de Ezequiel Solana", None).unscorable_reason == "no_zone"
    assert r.resolve("San Blas", "San Blas").name == "San Blas-Canillejas"
    assert r.resolve("Coslada", None).unscorable_reason == "out_of_scope"


@requires_test_db
@needs_csv
def test_resolve_atajo_y_equivalencia_con_el_resolvedor_en_memoria(seeded_db):
    memory, _ = build_resolver()
    db_resolver = load_resolver(seeded_db)
    cases = [("Moncloa - Aravaca", None), ("Palacio", "Chamberí"), ("Atlantis", "Retiro"), ("Coslada", "Centro"),
             (None, None), ("Calle de Ezequiel Solana", "Centro"), ("San Blas", "San Blas"), ("Atocha", None)]
    for n, d in cases:
        a, b = memory.resolve(n, d), db_resolver.resolve(n, d)
        assert (a.level, a.code, a.name, a.matched_on, a.alias, a.unscorable_reason, a.detail) == \
               (b.level, b.code, b.name, b.matched_on, b.alias, b.unscorable_reason, b.detail), (n, d)
        assert resolve(seeded_db, n, d) == b


@requires_test_db
@needs_csv
def test_el_resolvedor_no_consulta_la_base_por_anuncio(seeded_db):
    from sqlalchemy import event
    r = load_resolver(seeded_db)
    statements = []
    event.listen(seeded_db.get_bind(), "before_cursor_execute", lambda *a: statements.append(a[2]))
    for _ in range(50):
        r.resolve("Moncloa - Aravaca", "Moncloa-Aravaca")
    assert statements == []


def test_la_normalizacion_es_la_misma_que_la_de_las_zonas():
    assert normalize_zone_name("Moncloa - Aravaca") == "moncloa aravaca"
