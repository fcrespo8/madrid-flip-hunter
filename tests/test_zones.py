"""zones / zone_aliases: normalización, zonas a partir del CSV real, migración y siembra idempotente."""
import ast
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from backend.market.registral import RegistralRow, load_registral_csv, parse_registral_text
from backend.market.zones import (
    ZoneSeedError, ZoneSpec, normalize_zone_name, seed_zones, zone_specs_from_rows,
)
from tests.conftest import requires_test_db

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "precio_registral_barrio.csv"
needs_csv = pytest.mark.skipif(not CSV_PATH.exists(), reason="data/raw/precio_registral_barrio.csv no está")

HEADER = "Distrito;Barrio;Año;Precio medio y números índice;Tipo de vivienda;Total\n"


def rows_of(*lines) -> list[RegistralRow]:
    return parse_registral_text(HEADER + "\n".join(lines) + "\n")


MINI = [
    "Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;5.333,59",
    "01. Centro;01. Centro;2025;Euros/m2;Usadas;7.171,36",
    "01. Centro;011. Palacio;2025;Euros/m2;Usadas;6.954,58",
    "02. Arganzuela;02. Arganzuela;2025;Euros/m2;Usadas;5.648,69",
    "02. Arganzuela;027. Atocha;2025;Euros/m2;Usadas;0,00",
    "09. Moncloa-Aravaca;09. Moncloa-Aravaca;2025;Euros/m2;Usadas;5.869,93",
    "17. Villaverde;17. Villaverde;2025;Euros/m2;Usadas;2.649,60",
    "17. Villaverde;171. Villaverde Alto, Casco Histórico de Villaverde;2025;Euros/m2;Usadas;2.600,00",
]


# ── Normalización ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("Chamartín", "chamartin"),
    ("Moncloa-Aravaca", "moncloa aravaca"),
    ("Moncloa - Aravaca", "moncloa aravaca"),          # la variante de los portales normaliza igual
    ("Villaverde Alto, Casco Histórico de Villaverde", "villaverde alto casco historico de villaverde"),
    ("Argüelles", "arguelles"),
    ("Niño Jesús", "nino jesus"),
    ("  Los   Ángeles ", "los angeles"),
    ("San Blas-Canillejas", "san blas canillejas"),
    ("12 de Octubre-Orcasur", "12 de octubre orcasur"),
    ("Ciudad de Madrid", "ciudad de madrid"),
    ("", ""),
])
def test_normalize_zone_name(name, expected):
    assert normalize_zone_name(name) == expected


# ── Zonas a partir de las filas ────────────────────────────────────────────────

def test_zone_specs_de_un_fragmento():
    specs = zone_specs_from_rows(rows_of(*MINI))
    assert [(s.level, s.code, s.name) for s in specs] == [
        ("ciudad", None, "Ciudad de Madrid"), ("distrito", "01", "Centro"), ("distrito", "02", "Arganzuela"),
        ("distrito", "09", "Moncloa-Aravaca"), ("distrito", "17", "Villaverde"),
        ("barrio", "011", "Palacio"), ("barrio", "027", "Atocha"),
        ("barrio", "171", "Villaverde Alto, Casco Histórico de Villaverde"),
    ]
    by_key = {s.key: s for s in specs}
    assert by_key[("ciudad", None)].parent is None
    assert by_key[("distrito", "01")].parent == ("ciudad", None)
    assert by_key[("barrio", "011")].parent == ("distrito", "01")
    assert by_key[("distrito", "09")].name_norm == "moncloa aravaca"


def test_una_zona_sin_dato_de_precio_igual_es_una_zona():
    """Atocha tiene 0,00 en 2025, pero es un barrio oficial: hay que sembrarlo."""
    assert ("barrio", "027") in {s.key for s in zone_specs_from_rows(rows_of(*MINI))}


def test_los_padres_vienen_antes_que_los_hijos():
    specs = zone_specs_from_rows(rows_of(*MINI))
    position = {s.key: i for i, s in enumerate(specs)}
    assert all(position[s.parent] < position[s.key] for s in specs if s.parent)


def test_un_barrio_sin_su_distrito_en_el_archivo_es_un_error():
    with pytest.raises(ZoneSeedError, match="su padre"):
        zone_specs_from_rows(rows_of(MINI[0], MINI[2]))


def test_un_codigo_con_dos_nombres_es_un_error():
    rows = rows_of(MINI[0], MINI[1], MINI[1].replace("Centro", "Centro Histórico").replace("2025", "2024"))
    with pytest.raises(ZoneSeedError, match="dos nombres"):
        zone_specs_from_rows(rows)


def test_dos_zonas_con_el_mismo_nombre_normalizado_son_un_error():
    rows = rows_of(MINI[0], MINI[1], "01. Centro;011. Centro;2025;Euros/m2;Usadas;6.954,58")
    with pytest.raises(ZoneSeedError, match="mismo nombre normalizado"):
        zone_specs_from_rows(rows)


@needs_csv
def test_zone_specs_del_csv_real():
    specs = zone_specs_from_rows(load_registral_csv(CSV_PATH))
    assert len(specs) == 153
    assert Counter(s.level for s in specs) == {"ciudad": 1, "distrito": 21, "barrio": 131}
    assert len({s.name_norm for s in specs}) == 153                                    # sin colisiones
    assert len({s.key for s in specs}) == 153
    districts = {s.code: s for s in specs if s.level == "distrito"}
    assert all(s.parent == ("distrito", s.code[:2]) for s in specs if s.level == "barrio")
    assert all(s.code[:2] in districts for s in specs if s.level == "barrio")
    assert districts["09"].name == "Moncloa-Aravaca" and districts["20"].name_norm == "san blas canillejas"
    by_code = {s.code: s for s in specs if s.level == "barrio"}
    assert by_code["027"].name == "Atocha" and by_code["212"].name == "Aeropuerto"     # los dos sin dato en 2025
    assert by_code["026"].name == "Palos de la Frontera"
    assert Counter(s.parent[1] for s in specs if s.level == "barrio")["01"] == 6      # Centro tiene 6 barrios


# ── Modelo y migración ─────────────────────────────────────────────────────────

def test_modelo_zone():
    from backend.models.zone import Zone
    t = Zone.__table__
    assert set(t.c.keys()) == {"id", "level", "code", "name", "name_norm", "parent_id"}
    assert {c.name for c in t.c if c.nullable} == {"code", "parent_id"}
    assert {c.name for c in t.constraints if c.name} >= {"ck_zones_level", "uq_zones_level_code"}


def test_modelo_zone_alias():
    from backend.models.zone import ZoneAlias
    t = ZoneAlias.__table__
    assert set(t.c.keys()) == {"id", "alias_norm", "zone_id", "kind", "source", "note"}
    assert {c.name for c in t.c if c.nullable} == {"zone_id", "note"}
    assert "uq_zone_aliases_alias_norm" in {c.name for c in t.constraints}


def _migration_tree():
    path = next((ROOT / "alembic" / "versions").glob("*_add_zones_and_zone_aliases.py"))
    return ast.parse(path.read_text())


def test_la_migracion_crea_las_mismas_columnas_que_el_modelo():
    from backend.models.zone import Zone, ZoneAlias
    created = {}
    for node in ast.walk(_migration_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "create_table":
            columns = {c.args[0].value for c in node.args[1:] if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "Column"}
            created[node.args[0].value] = columns
    assert created == {"zones": set(Zone.__table__.c.keys()), "zone_aliases": set(ZoneAlias.__table__.c.keys())}


def test_la_migracion_se_deshace_en_orden_inverso():
    tree = _migration_tree()
    down = next(f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == "downgrade")
    dropped = [n.args[0].value for n in ast.walk(down) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "drop_table"]
    assert dropped == ["zone_aliases", "zones"]                                       # el alias depende de la zona


def test_la_migracion_cuelga_de_pre_score_y_no_hay_ramas():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert script.get_revision("e6f1b3d7c2a5").down_revision == "d5e9a2c6b1f4"


# ── Siembra contra la base de prueba ───────────────────────────────────────────

@pytest.fixture
def clean_zones():
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


def _counts():
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias
    db = SessionLocal()
    try:
        return db.query(Zone).count(), db.query(ZoneAlias).count()
    finally:
        db.close()


def _seed(specs, commit=True):
    from backend.models.database import SessionLocal
    db = SessionLocal()
    try:
        result = seed_zones(db, specs)
        db.commit() if commit else db.rollback()
        return result
    finally:
        db.close()


@requires_test_db
def test_siembra_crea_zonas_y_un_alias_oficial_por_zona(clean_zones):
    specs = zone_specs_from_rows(rows_of(*MINI))
    result = _seed(specs)
    assert (result.zones_created, result.aliases_created, result.zones_unchanged) == (8, 8, 0)
    assert _counts() == (8, 8)

    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias
    db = SessionLocal()
    zones = {(z.level, z.code): z for z in db.query(Zone)}
    centro, palacio, city = zones[("distrito", "01")], zones[("barrio", "011")], zones[("ciudad", None)]
    assert (palacio.parent_id, centro.parent_id, city.parent_id) == (centro.id, city.id, None)
    aliases = {a.alias_norm: a for a in db.query(ZoneAlias)}
    assert aliases["moncloa aravaca"].zone_id == zones[("distrito", "09")].id and aliases["moncloa aravaca"].kind == "distrito"
    assert aliases["palacio"].kind == "barrio" and aliases["ciudad de madrid"].kind == "ciudad"
    assert all(a.source == "official" for a in aliases.values())
    assert {a.zone_id for a in aliases.values()} == {z.id for z in zones.values()}        # cada zona tiene su alias
    db.close()


@requires_test_db
def test_correrlo_dos_veces_no_duplica_nada(clean_zones):
    specs = zone_specs_from_rows(rows_of(*MINI))
    _seed(specs)
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone
    db = SessionLocal()
    ids = {(z.level, z.code): z.id for z in db.query(Zone)}
    db.close()

    again = _seed(specs)
    assert (again.zones_created, again.zones_updated, again.aliases_created, again.aliases_updated) == (0, 0, 0, 0)
    assert again.zones_unchanged == 8 and not again.changed
    assert _counts() == (8, 8)
    db = SessionLocal()
    assert {(z.level, z.code): z.id for z in db.query(Zone)} == ids                          # los ids no cambian
    db.close()


@requires_test_db
def test_dry_run_no_deja_nada(clean_zones):
    result = _seed(zone_specs_from_rows(rows_of(*MINI)), commit=False)
    assert result.zones_created == 8
    assert _counts() == (0, 0)


@requires_test_db
def test_un_cambio_de_nombre_actualiza_la_zona_y_suma_el_alias_nuevo(clean_zones):
    specs = zone_specs_from_rows(rows_of(*MINI))
    _seed(specs)
    renamed = [ZoneSpec(s.level, s.code, "Palacio Real", normalize_zone_name("Palacio Real"), s.parent)
               if s.key == ("barrio", "011") else s for s in specs]
    result = _seed(renamed)
    assert (result.zones_updated, result.zones_created, result.aliases_created) == (1, 0, 1)
    assert _counts() == (8, 9)                                                               # el alias viejo se conserva

    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias
    db = SessionLocal()
    zone = db.query(Zone).filter_by(level="barrio", code="011").one()
    assert zone.name == "Palacio Real"
    assert {a.alias_norm for a in db.query(ZoneAlias).filter_by(zone_id=zone.id)} == {"palacio", "palacio real"}
    db.close()


@requires_test_db
def test_sembrar_no_toca_los_alias_manuales(clean_zones):
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias
    specs = zone_specs_from_rows(rows_of(*MINI))
    _seed(specs)
    db = SessionLocal()
    zone = db.query(Zone).filter_by(level="barrio", code="027").one()
    db.add(ZoneAlias(alias_norm="atocha centro", zone_id=zone.id, kind="barrio", source="manual", note="a mano"))
    db.add(ZoneAlias(alias_norm="coslada", zone_id=None, kind="out_of_scope", source="manual"))
    db.commit()
    db.close()

    assert not _seed(specs).changed
    assert _counts() == (8, 10)


@requires_test_db
def test_un_alias_que_apunta_a_otra_zona_aborta_sin_pisarlo(clean_zones):
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias
    specs = zone_specs_from_rows(rows_of(*MINI))
    _seed(specs)
    db = SessionLocal()
    centro_id = db.query(Zone).filter_by(level="distrito", code="01").one().id
    palacio_alias = db.query(ZoneAlias).filter_by(alias_norm="palacio").one()
    palacio_alias.zone_id = centro_id                                 # alguien lo redirigió a mano
    db.commit()
    db.close()

    with pytest.raises(ZoneSeedError, match="ya apunta a otra zona"):
        _seed(specs)
    db = SessionLocal()
    assert db.query(ZoneAlias).filter_by(alias_norm="palacio").one().zone_id == centro_id
    db.close()


@requires_test_db
def test_zonas_de_la_base_que_no_estan_en_el_csv_se_cuentan_y_no_se_borran(clean_zones):
    specs = zone_specs_from_rows(rows_of(*MINI))
    _seed(specs)
    result = _seed([s for s in specs if s.key != ("barrio", "027")])
    assert result.zones_not_in_source == 1 and not result.changed
    assert _counts() == (8, 8)


@requires_test_db
def test_restricciones_de_la_base(clean_zones):
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias
    _seed(zone_specs_from_rows(rows_of(*MINI)))
    db = SessionLocal()
    zone_id = db.query(Zone).filter_by(level="barrio", code="011").one().id

    def fails(obj):
        db.add(obj)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    fails(Zone(level="manzana", code="9", name="x", name_norm="x"))                              # nivel inválido
    fails(Zone(level="barrio", code="011", name="otra", name_norm="otra"))                       # (nivel, código) repetido
    fails(Zone(level="ciudad", code=None, name="Madrid", name_norm="madrid"))                    # una sola ciudad (código NULL)
    fails(Zone(level="ciudad", code="00", name="Madrid", name_norm="madrid"))
    fails(ZoneAlias(alias_norm="palacio", zone_id=zone_id, kind="barrio", source="manual"))      # alias repetido
    fails(ZoneAlias(alias_norm="nuevo", zone_id=zone_id, kind="calle", source="manual"))         # tipo inválido
    fails(ZoneAlias(alias_norm="nuevo", zone_id=zone_id, kind="barrio", source="web"))           # origen inválido
    fails(ZoneAlias(alias_norm="nuevo", zone_id=None, kind="barrio", source="manual"))           # barrio sin zona
    fails(ZoneAlias(alias_norm="nuevo", zone_id=zone_id, kind="out_of_scope", source="manual"))  # fuera de alcance con zona
    db.close()
    assert _counts() == (8, 8)


# ── El script ──────────────────────────────────────────────────────────────────

@requires_test_db
@needs_csv
def test_script_dry_run_no_escribe(clean_zones, capsys):
    from scripts import seed_zones as script
    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert "zonas en el CSV: 153" in out and "153 nuevas" in out and "[dry-run]" in out
    assert _counts() == (0, 0)


@requires_test_db
@needs_csv
def test_script_confirm_siembra_153_zonas_y_es_idempotente(clean_zones, capsys):
    from scripts import seed_zones as script
    assert script.main(["--confirm"]) == 0
    assert _counts() == (153, 153)
    from backend.models.database import SessionLocal
    from backend.models.zone import Zone, ZoneAlias
    db = SessionLocal()
    assert Counter(z.level for z in db.query(Zone)) == {"ciudad": 1, "distrito": 21, "barrio": 131}
    assert db.query(ZoneAlias).filter_by(source="official").count() == 153
    assert db.query(Zone).filter(Zone.parent_id.is_(None)).count() == 1                     # solo la ciudad
    db.close()

    capsys.readouterr()
    assert script.main(["--confirm"]) == 0
    out = capsys.readouterr().out
    assert "0 nuevas" in out and "0 actualizadas" in out and "ya estaba sembrado" in out
    assert _counts() == (153, 153)


@requires_test_db
def test_script_con_un_csv_roto_no_toca_la_base(clean_zones, tmp_path, capsys):
    from scripts import seed_zones as script
    bad = tmp_path / "roto.csv"
    bad.write_text("esto;no;es;el;archivo\n")
    assert script.main(["--csv", str(bad), "--confirm"]) == script.EXIT_BAD_INPUT
    assert "ERROR" in capsys.readouterr().out
    assert script.main(["--csv", str(tmp_path / "no_existe.csv")]) == script.EXIT_BAD_INPUT
    assert _counts() == (0, 0)
