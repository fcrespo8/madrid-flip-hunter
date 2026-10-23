"""Parser del CSV de precio registral por barrio. Puro: sin base de datos ni red."""
import hashlib
from collections import Counter
from pathlib import Path

import pytest

from backend.market.registral import (
    RegistralParseError, RegistralRow, latest_year, load_registral_csv, parse_price, parse_registral_text,
)

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "raw" / "precio_registral_barrio.csv"

# Líneas reales del archivo (cabecera con BOM incluida).
FRAGMENT = (
    "\ufeffDistrito;Barrio;Año;Precio medio y números índice;Tipo de vivienda;Total\n"
    "Ciudad de Madrid;Ciudad de Madrid;2024;Euros/m2;Usadas;4.544,27\n"
    "Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;5.333,59\n"
    "01. Centro;01. Centro;2024;Euros/m2;Usadas;6.279,87\n"
    "01. Centro;01. Centro;2025;Euros/m2;Usadas;7.171,36\n"
    "01. Centro;011. Palacio;2007;Euros/m2;Usadas;..\n"
    "01. Centro;011. Palacio;2016;Euros/m2;Usadas;3.257,02\n"
    "01. Centro;011. Palacio;2025;Euros/m2;Usadas;6.954,58\n"
    "02. Arganzuela;02. Arganzuela;2025;Euros/m2;Usadas;5.648,69\n"
    "02. Arganzuela;027. Atocha;2023;Euros/m2;Usadas;0,00\n"
    "02. Arganzuela;027. Atocha;2024;Euros/m2;Usadas;0,00\n"
    "02. Arganzuela;027. Atocha;2025;Euros/m2;Usadas;0,00\n"
    "18. Villa de Vallecas;183. Ensanche de Vallecas;2016;Euros/m2;Usadas;..\n"
    "21. Barajas;21. Barajas;2025;Euros/m2;Usadas;4.413,92\n"
    "21. Barajas;211. Alameda de Osuna;2025;Euros/m2;Usadas;4.710,05\n"
    "21. Barajas;212. Aeropuerto;2025;Euros/m2;Usadas;0,00\n"
)


def by_key(rows):
    return {(r.year, r.district_name, r.barrio_name): r for r in rows}


# ── Números ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("5.333,59", 5333.59),
    ("1.234.567,5", 1234567.5),
    ("999,99", 999.99),
    ("4413,92", 4413.92),
    ("2500", 2500.0),
    (" 6.954,58 ", 6954.58),
    ("..", None),                 # barrio no publicado
    ("0,00", None),               # barrio con menos de 15 compraventas
    ("0", None),
    ("0,0", None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "...", "n/d", "5,333.59", "5.33,59", "12a", "-1,00", "1,2,3", "5.333,"])
def test_parse_price_rechaza_lo_que_no_es_un_numero_espanol(raw):
    with pytest.raises(ValueError):
        parse_price(raw)


# ── Fragmento real ─────────────────────────────────────────────────────────────

def test_fragmento_real_filas_y_niveles():
    rows = parse_registral_text(FRAGMENT)
    assert len(rows) == 15
    assert Counter(r.level for r in rows) == {"ciudad": 2, "distrito": 4, "barrio": 9}
    assert all(r.housing_type == "usadas" for r in rows)


def test_la_fila_de_la_ciudad_no_tiene_distrito_ni_barrio():
    city = by_key(parse_registral_text(FRAGMENT))[(2025, None, None)]
    assert city == RegistralRow(2025, "ciudad", None, None, None, None, "usadas", 5333.59)


def test_una_fila_de_distrito_no_tiene_barrio():
    centro = by_key(parse_registral_text(FRAGMENT))[(2025, "Centro", None)]
    assert (centro.level, centro.district_code, centro.barrio_code, centro.barrio_name) == ("distrito", "01", None, None)
    assert centro.price_m2 == 7171.36


def test_una_fila_de_barrio_separa_codigo_y_nombre():
    palacio = by_key(parse_registral_text(FRAGMENT))[(2025, "Centro", "Palacio")]
    assert palacio == RegistralRow(2025, "barrio", "01", "Centro", "011", "Palacio", "usadas", 6954.58)


def test_el_bom_de_la_cabecera_se_ignora():
    assert len(parse_registral_text(FRAGMENT)) == len(parse_registral_text(FRAGMENT.lstrip("\ufeff")))


@pytest.mark.parametrize("year, district, barrio, level", [
    (2025, "Arganzuela", "Atocha", "barrio"),
    (2024, "Arganzuela", "Atocha", "barrio"),
    (2023, "Arganzuela", "Atocha", "barrio"),
    (2025, "Barajas", "Aeropuerto", "barrio"),
])
def test_atocha_y_aeropuerto_con_cero_son_sin_dato(year, district, barrio, level):
    """'0,00' es 'menos de 15 compraventas', no un precio de cero euros."""
    row = by_key(parse_registral_text(FRAGMENT))[(year, district, barrio)]
    assert (row.level, row.price_m2) == (level, None)


def test_el_punto_punto_es_sin_dato():
    rows = by_key(parse_registral_text(FRAGMENT))
    assert rows[(2007, "Centro", "Palacio")].price_m2 is None
    assert rows[(2016, "Villa de Vallecas", "Ensanche de Vallecas")].price_m2 is None


def test_los_vecinos_de_un_sin_dato_conservan_su_precio():
    rows = by_key(parse_registral_text(FRAGMENT))
    assert rows[(2025, "Barajas", "Alameda de Osuna")].price_m2 == 4710.05      # al lado de Aeropuerto (sin dato)
    assert rows[(2016, "Centro", "Palacio")].price_m2 == 3257.02                # entre un '..' (2007) y los años buenos


def test_latest_year():
    assert latest_year(parse_registral_text(FRAGMENT)) == 2025


# ── Formato ────────────────────────────────────────────────────────────────────

def test_finales_de_linea_windows():
    assert len(parse_registral_text(FRAGMENT.replace("\n", "\r\n"))) == 15


def test_lineas_en_blanco_se_ignoran():
    assert len(parse_registral_text(FRAGMENT + "\n\n")) == 15


# ── Errores: el parser es estricto ─────────────────────────────────────────────

HEADER = "Distrito;Barrio;Año;Precio medio y números índice;Tipo de vivienda;Total\n"


def parse_body(*lines):
    return parse_registral_text(HEADER + "\n".join(lines) + "\n")


@pytest.mark.parametrize("text, message", [
    ("Distrito;Barrio;Año\nCiudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;1,00\n", "línea 1: cabecera"),
    (HEADER, "no tiene filas"),
    ("", "no tiene filas"),
])
def test_cabecera_y_archivo_vacio(text, message):
    with pytest.raises(RegistralParseError, match=message):
        parse_registral_text(text)


@pytest.mark.parametrize("line, message", [
    ("Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas", "línea 2: 5 columnas"),
    ("Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;1,00;x", "línea 2: 7 columnas"),
    ("Ciudad de Madrid;Ciudad de Madrid;2025;Número índice;Usadas;100,00", "unidad 'Número índice'"),
    ("Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Mixtas;1,00", "tipo de vivienda desconocido"),
    ("Ciudad de Madrid;Ciudad de Madrid;25;Euros/m2;Usadas;1,00", "año no válido"),
    ("Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;cinco", "línea 2: número no válido"),
    ("Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;", "número no válido"),
    ("Ciudad de Madrid;01. Centro;2025;Euros/m2;Usadas;1,00", "fila de la ciudad con barrio"),
    ("01. Centro;021. Imperial;2025;Euros/m2;Usadas;1,00", "no cuelga del distrito"),
    ("Centro;011. Palacio;2025;Euros/m2;Usadas;1,00", "no cuelga del distrito"),     # distrito sin código
])
def test_filas_mal_formadas(line, message):
    with pytest.raises(RegistralParseError, match=message):
        parse_body(line)


def test_el_error_indica_la_linea_correcta():
    with pytest.raises(RegistralParseError, match="línea 4"):
        parse_body("Ciudad de Madrid;Ciudad de Madrid;2024;Euros/m2;Usadas;1,00",
                   "Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;2,00",
                   "Ciudad de Madrid;Ciudad de Madrid;2026;Euros/m2;Usadas;oops")


def test_fila_repetida():
    line = "Ciudad de Madrid;Ciudad de Madrid;2025;Euros/m2;Usadas;1,00"
    with pytest.raises(RegistralParseError, match="línea 3: fila repetida"):
        parse_body(line, line)


# ── El archivo real ────────────────────────────────────────────────────────────

pytestmark_real = pytest.mark.skipif(not CSV_PATH.exists(), reason="data/raw/precio_registral_barrio.csv no está")


@pytest.fixture(scope="module")
def real_rows():
    return load_registral_csv(CSV_PATH)


@pytestmark_real
def test_el_archivo_real_tiene_la_estructura_documentada(real_rows):
    assert len(real_rows) == 2907
    per_year = Counter(r.year for r in real_rows)
    assert sorted(per_year) == list(range(2007, 2026)) and set(per_year.values()) == {153}
    assert Counter(r.level for r in real_rows if r.year == 2025) == {"ciudad": 1, "distrito": 21, "barrio": 131}
    assert {r.housing_type for r in real_rows} == {"usadas"}


@pytestmark_real
def test_el_archivo_real_ciudad_2025(real_rows):
    city = [r for r in real_rows if r.year == 2025 and r.level == "ciudad"]
    assert [r.price_m2 for r in city] == [5333.59]


@pytestmark_real
def test_el_archivo_real_cuenta_los_sin_dato_como_dice_el_readme(real_rows):
    none = [r for r in real_rows if r.price_m2 is None]
    assert len(none) == 1182 + 49                                           # '..' + '0,00'
    assert all(r.level == "barrio" for r in none)                           # distritos y ciudad siempre tienen precio
    assert {r.barrio_name for r in none if r.year == 2025} == {"Atocha", "Aeropuerto"}
    assert all(r.price_m2 > 0 for r in real_rows if r.price_m2 is not None)


@pytestmark_real
def test_el_archivo_real_barrios_publicados_en_2025(real_rows):
    published = [r for r in real_rows if r.year == 2025 and r.level == "barrio" and r.price_m2 is not None]
    assert len(published) == 129
    assert all(1000 < r.price_m2 < 20000 for r in published)               # nada absurdo (un 0 o un índice colado)


@pytestmark_real
def test_el_archivo_real_codigos_consistentes(real_rows):
    districts = {(r.district_code, r.district_name) for r in real_rows if r.level == "distrito"}
    assert len(districts) == 21 and ("20", "San Blas-Canillejas") in districts
    barrios = {(r.district_code, r.barrio_code, r.barrio_name) for r in real_rows if r.level == "barrio"}
    assert len(barrios) == 131 and all(b.startswith(d) for d, b, _ in barrios)
    assert ("02", "027", "Atocha") in barrios and ("21", "212", "Aeropuerto") in barrios


@pytestmark_real
def test_el_readme_documenta_el_sha256_del_archivo_real():
    sha = hashlib.sha256(CSV_PATH.read_bytes()).hexdigest()
    assert sha in (ROOT / "data" / "raw" / "README.md").read_text()


@pytestmark_real
def test_el_fragmento_coincide_con_el_archivo_real():
    """El fragmento de arriba son líneas reales: si el archivo cambia, hay que actualizarlo."""
    real = CSV_PATH.read_text(encoding="utf-8").lstrip("\ufeff").splitlines()
    for line in FRAGMENT.lstrip("\ufeff").splitlines():
        assert line in real, line
