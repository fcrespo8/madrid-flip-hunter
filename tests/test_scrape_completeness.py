"""Lógica de `complete` de los scrapers (ScrapeResult), scrape_runs y su uso en el pipeline.
Offline: los scrapers corren contra una sesión HTTP falsa; nada sale a la red."""
import asyncio
import io
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

from backend.scrapers import base_scraper as bs
from backend.scrapers.base_scraper import PageWalk, RawListing, ScrapeResult, as_count
from tests.conftest import requires_test_db
from tests.test_scoring import FakeDB, _offline  # noqa: F401


def raw(n, source="fake"):
    return RawListing(source=source, external_id=f"id{n}", url=f"u{n}", title="t", price=1.0, size_m2=None,
                      rooms=None, neighborhood=None, district=None, lat=None, lon=None, description=None)


def raws(*ns):
    return [raw(n) for n in ns]


# ── ScrapeResult.finish: la regla de `complete` ────────────────────────────────

def test_completo_cuando_llego_al_final_y_cubre_el_total():
    r = ScrapeResult.finish(raws(*range(10)), reached_end=True, total_reported=10)
    assert (r.complete, r.incomplete_reason) == (True, None)


def test_completo_sin_total_informado():
    """Si el sitio no muestra su total, alcanza con llegar al final natural."""
    assert ScrapeResult.finish(raws(1, 2), reached_end=True, total_reported=None).complete is True


@pytest.mark.parametrize("kwargs, reason", [
    (dict(reached_end=False, stop_reason=bs.PAGE_CAP), "page_cap"),
    (dict(reached_end=False, stop_reason=bs.REPEATED_PAGE), "repeated_page"),
    (dict(reached_end=False, stop_reason=bs.NO_PAGINATION), "no_pagination"),
    (dict(reached_end=False, stop_reason=bs.UNPARSEABLE), "unparseable_page"),
    (dict(reached_end=False), "page_cap"),                        # sin motivo explícito: asumir tope
    (dict(reached_end=True, error="timeout"), "error"),           # el error gana aunque haya llegado al final
    (dict(reached_end=False, stop_reason=bs.PAGE_CAP, error="HTTP 500"), "error"),
])
def test_incompleto_por_motivo(kwargs, reason):
    r = ScrapeResult.finish(raws(1, 2, 3), **kwargs)
    assert (r.complete, r.incomplete_reason) == (False, reason)


def test_vacio_nunca_es_completo():
    """Un scraper bloqueado que devuelve [] no puede desactivar toda su fuente."""
    r = ScrapeResult.finish([], reached_end=True)
    assert (r.complete, r.incomplete_reason) == (False, "empty")


def test_failed():
    r = ScrapeResult.failed("Chromium no instalado")
    assert (r.complete, r.incomplete_reason, r.error, r.listings) == (False, "error", "Chromium no instalado", [])


@pytest.mark.parametrize("seen, complete", [
    (90, True),      # exactamente 90 %: alcanza
    (89, False),
    (100, True),
    (142, True),     # más de lo informado (el total se actualizó mientras se barría)
])
def test_umbral_de_cobertura_del_90_por_ciento(seen, complete):
    r = ScrapeResult.finish(raws(*range(seen)), reached_end=True, total_reported=100)
    assert r.complete is complete
    assert r.incomplete_reason == (None if complete else "low_coverage")


def test_cobertura_cuenta_lo_que_mostro_el_sitio_no_lo_que_se_guarda():
    """Redpiso descarta otros municipios: 16 guardados de 20 vistos en un total de 20."""
    r = ScrapeResult.finish(raws(*range(16)), reached_end=True, total_reported=20, items_seen=20)
    assert r.complete is True and r.items_seen == 20
    sin_items = ScrapeResult.finish(raws(*range(16)), reached_end=True, total_reported=20)
    assert (sin_items.complete, sin_items.incomplete_reason) == (False, "low_coverage")


def test_no_llegar_al_final_gana_a_la_cobertura():
    r = ScrapeResult.finish(raws(*range(100)), reached_end=False, stop_reason=bs.PAGE_CAP, total_reported=100)
    assert r.incomplete_reason == "page_cap"


def test_finish_deduplica_por_external_id():
    r = ScrapeResult.finish(raws(1, 1, 2, 2, 2), reached_end=True)
    assert [x.external_id for x in r.listings] == ["id1", "id2"]


@pytest.mark.parametrize("value, expected", [("1.342", 1342), (926, 926), ("142", 142), (" 15 ", 15),
                                             (None, None), ("", None), ("n/d", None), (0, None), (-3, None)])
def test_as_count(value, expected):
    assert as_count(value) == expected


# ── PageWalk: detección de páginas repetidas ───────────────────────────────────

def test_pagewalk_acumula_y_detecta_pagina_repetida():
    walk = PageWalk()
    assert walk.add_page(raws(1, 2, 3)) is True
    assert walk.add_page(raws(4, 5), site_items=3) is True
    assert walk.add_page(raws(1, 2, 3)) is False          # misma página: el sitio ignora el parámetro
    assert walk.add_page(raws(4, 5)) is False
    assert [x.external_id for x in walk.listings] == ["id1", "id2", "id3", "id4", "id5"]
    assert walk.items_seen == 3 + 3                       # la repetida no suma


def test_pagewalk_pagina_parcialmente_nueva_no_es_repetida():
    walk = PageWalk()
    walk.add_page(raws(1, 2, 3))
    assert walk.add_page(raws(3, 4)) is True
    assert [x.external_id for x in walk.listings] == ["id1", "id2", "id3", "id4"]


# ── Parseo del total que informa cada sitio ────────────────────────────────────

def soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def test_redpiso_parse_total():
    from backend.scrapers.redpiso_scraper import parse_total
    assert parse_total(soup("<h1>1.342 viviendas en venta en Madrid</h1>")) == 1342
    assert parse_total(soup("<p>algo</p><span>987 viviendas en venta</span>")) == 987   # sin h1: texto
    assert parse_total(soup("<h1>Pisos y casas en venta</h1>")) is None


def test_remax_parse_total():
    from backend.scrapers.remax_scraper import parse_total
    assert parse_total(soup("<span>Propiedades encontradas: 142</span> 1 2 3 4 5")) == 142
    assert parse_total(soup("<span>Propiedades encontradas: 1.250</span>")) == 1250
    assert parse_total(soup("<p>sin total</p>")) is None


# ── Scrapers contra una sesión HTTP falsa ──────────────────────────────────────

class FakeResp:
    def __init__(self, text="", json_data=None, status=200):
        self.text, self._json, self.status = text, json_data, status
        self.status_code = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"HTTP {self.status}", response=self)

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, handler):
        self.handler, self.headers, self.calls = handler, {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.handler(url, dict(params or {}))


@pytest.fixture
def http(monkeypatch):
    """http(handler) instala una sesión falsa en requests y devuelve la sesión (con .calls).
    http.sleeps lista las esperas pedidas con time.sleep (nada duerme de verdad)."""
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)

    def install(handler):
        session = FakeSession(handler)
        monkeypatch.setattr(requests, "Session", lambda: session)
        return session
    install.sleeps = sleeps
    return install


# --- tecnocasa ---

def estate(n, price=True):
    return {"id": n, "price": "175.000 €" if price else "", "surface": "50 m<sup>2</sup>",
            "rooms": "2 dorm.", "subtitle": "Madrid, Tetuán", "title": f"Piso {n}"}


def tecnocasa_api(pages, total_items, total_pages=None, failures=None, repeat_from=None, pagination="auto"):
    """pages: dict página → lista de estates (las que no están vienen vacías).
    failures: dict página → (status o excepción, veces): esa página falla `veces` veces y después responde bien.
    pagination: "auto" arma {total_items, total_pages}; un dict se usa tal cual; None omite el bloque."""
    hits = {}

    def handler(url, params):
        if url.endswith("/search-map-list"):
            return FakeResp(json_data={"collection": {"features": []}})
        page = params.get("page", 1)
        hits[page] = hits.get(page, 0) + 1
        code, times = (failures or {}).get(page, (None, 0))
        if code is not None and hits[page] <= times:
            if isinstance(code, Exception):
                raise code
            return FakeResp(status=code)
        if repeat_from and page >= repeat_from:
            page = 1
        body = {"estates": pages.get(page, [])}
        if pagination == "auto":
            body["pagination"] = {"total_items": total_items, "total_pages": total_pages or max(pages, default=1)}
        elif pagination is not None:
            body["pagination"] = pagination
        return FakeResp(json_data=body)
    return handler


def run_tecnocasa(http, **api):
    from backend.scrapers.tecnocasa_scraper import TecnocasaScraper
    session = http(tecnocasa_api(**api))
    return TecnocasaScraper()._fetch_sync(), session


def search_calls(session, page=None):
    calls = [c for c in session.calls if c[0].endswith("/search")]
    return calls if page is None else [c for c in calls if c[1].get("page", 1) == page]


def full_pages(n, per_page=15):
    return {p: [estate(p * 100 + i) for i in range(1, per_page + 1)] for p in range(1, n + 1)}


# --- tecnocasa: hasta dónde recorre ---

def test_tecnocasa_barrido_completo(http):
    pages = {p: [estate(p * 100 + i, price=i != 0) for i in range(15)] for p in (1, 2, 3)}   # 1 sin precio por página
    result, _ = run_tecnocasa(http, pages=pages, total_items=45)
    assert len(result.listings) == 42                    # los 3 sin precio no se guardan...
    assert result.items_seen == 45                       # ...pero el sitio los mostró
    assert (result.complete, result.total_reported) == (True, 45)


def test_tecnocasa_recorre_hasta_total_pages_sin_tope_de_20(http):
    """Antes cortaba en la página 20 de 62. Ahora llega hasta la que informa la API."""
    result, session = run_tecnocasa(http, pages=full_pages(62), total_items=930, total_pages=62)
    assert len(search_calls(session)) == 62
    assert (len(result.listings), result.complete, result.incomplete_reason) == (930, True, None)


def test_tecnocasa_sigue_a_total_pages_si_cambia_a_mitad_del_barrido(http):
    """Si entran anuncios mientras se barre, total_pages crece: se usa el último valor informado."""
    def handler(url, params):
        if url.endswith("/search-map-list"):
            return FakeResp(json_data={"collection": {"features": []}})
        page = params.get("page", 1)
        total_pages = 3 if page == 1 else 4
        return FakeResp(json_data={"estates": [estate(page * 100 + i) for i in range(1, 16)],
                                   "pagination": {"total_items": 60, "total_pages": total_pages}})
    session = http(handler)
    from backend.scrapers.tecnocasa_scraper import TecnocasaScraper
    result = TecnocasaScraper()._fetch_sync()
    assert len(search_calls(session)) == 4 and result.complete is True


def test_tecnocasa_techo_de_seguridad_si_la_api_no_termina(http, monkeypatch):
    from backend.scrapers import tecnocasa_scraper
    monkeypatch.setattr(tecnocasa_scraper, "MAX_PAGES_SAFETY", 5)
    result, session = run_tecnocasa(http, pages=full_pages(50), total_items=750, total_pages=50)
    assert len(search_calls(session)) == 5 and len(result.listings) == 75
    assert (result.complete, result.incomplete_reason) == (False, "page_cap")


def test_tecnocasa_delay_de_1s_entre_paginas_y_ninguno_despues_de_la_ultima(http):
    run_tecnocasa(http, pages=full_pages(4), total_items=60)
    assert http.sleeps == [1.0, 1.0, 1.0]                # entre 4 páginas hay 3 pausas


def test_tecnocasa_pagina_repetida(http):
    pages = {1: [estate(i) for i in range(1, 16)], 2: [estate(i) for i in range(1, 16)]}
    result, _ = run_tecnocasa(http, pages=pages, total_items=30, total_pages=2, repeat_from=2)
    assert (result.complete, result.incomplete_reason) == (False, "repeated_page")
    assert len(result.listings) == 15


def test_tecnocasa_cobertura_baja(http):
    result, _ = run_tecnocasa(http, pages=full_pages(2), total_items=100, total_pages=2)    # termina, pero vio 30 de 100
    assert (result.complete, result.incomplete_reason) == (False, "low_coverage")


def test_tecnocasa_pagina_vacia_antes_de_lo_anunciado_es_fin(http):
    result, _ = run_tecnocasa(http, pages=full_pages(2), total_items=30, total_pages=5)     # la 3 viene vacía
    assert (result.complete, result.items_seen) == (True, 30)


# --- tecnocasa: la API no informa cuántas páginas hay ---

@pytest.mark.parametrize("pagination", [
    None,                                    # sin bloque de paginación
    {},
    {"total_items": 45},                     # sin total_pages
    {"total_items": 45, "total_pages": 0},
    {"total_items": 45, "total_pages": "n/d"},
])
def test_tecnocasa_sin_total_pages_valido_no_asume_que_termino(http, pagination):
    """Antes se asumía total_pages=1: la página 1 pasaba por el fin del catálogo y, sin total,
    el barrido salía COMPLETO con 15 listings (desactivación masiva)."""
    result, session = run_tecnocasa(http, pages=full_pages(3), total_items=45, pagination=pagination)
    assert (result.complete, result.incomplete_reason) == (False, "bad_pagination")
    assert len(search_calls(session)) == 1 and len(result.listings) == 15      # lo de la página 1 se conserva


# --- tecnocasa: reintentos ante error ---

def test_tecnocasa_reintenta_un_fallo_transitorio_y_sigue(http):
    result, session = run_tecnocasa(http, pages=full_pages(3), total_items=45, failures={2: (503, 1)})
    assert len(search_calls(session, page=2)) == 2                       # falló una vez, la segunda anduvo
    assert (result.complete, result.error) == (True, None) and len(result.listings) == 45
    assert http.sleeps == [1.0, 2.0, 1.0]                                # pausa p1→p2, backoff de 2 s, pausa p2→p3


def test_tecnocasa_corta_tras_agotar_los_reintentos_con_motivo_http_error(http):
    pages = full_pages(4)
    result, session = run_tecnocasa(http, pages=pages, total_items=60, failures={3: (503, 99)})
    assert len(search_calls(session, page=3)) == 3                       # 1 intento + 2 reintentos
    assert http.sleeps == [1.0, 1.0, 2.0, 4.0]                           # pausas p1→p2, p2→p3 y backoff 2 s, 4 s
    assert (result.complete, result.incomplete_reason) == (False, "http_error")
    assert result.error == "página 3 de 4: HTTPError: HTTP 503 (tras 3 intentos)"
    assert len(result.listings) == 30                                    # lo visto antes del corte se conserva
    assert len(search_calls(session, page=4)) == 0                       # y no sigue con la página 4


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_tecnocasa_un_4xx_no_se_reintenta(http, status):
    result, session = run_tecnocasa(http, pages=full_pages(3), total_items=45, failures={2: (status, 99)})
    assert len(search_calls(session, page=2)) == 1
    assert http.sleeps == [1.0]                                          # solo la pausa entre páginas, sin backoff
    assert result.error == f"página 2 de 3: HTTPError: HTTP {status} (tras 1 intento)"
    assert result.incomplete_reason == "http_error"


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_tecnocasa_reintenta_estos_status(http, status):
    result, session = run_tecnocasa(http, pages=full_pages(2), total_items=30, failures={2: (status, 1)})
    assert len(search_calls(session, page=2)) == 2 and result.complete is True


@pytest.mark.parametrize("exc", [requests.ConnectionError("reset"), requests.Timeout("timeout")])
def test_tecnocasa_reintenta_errores_de_red(http, exc):
    result, session = run_tecnocasa(http, pages=full_pages(2), total_items=30, failures={1: (exc, 2)})
    assert len(search_calls(session, page=1)) == 3                       # falló 2 veces, anduvo a la tercera
    assert http.sleeps == [2.0, 4.0, 1.0]
    assert result.complete is True


def test_tecnocasa_error_en_la_primera_pagina(http):
    result, session = run_tecnocasa(http, pages=full_pages(3), total_items=45,
                                    failures={1: (requests.ConnectionError("sin red"), 99)})
    assert result.error == "página 1: ConnectionError: sin red (tras 3 intentos)"     # aún no se conoce el total
    assert (result.listings, result.complete, result.incomplete_reason) == ([], False, "http_error")


def test_tecnocasa_el_error_de_http_no_se_confunde_con_el_generico():
    """'error' es de un scraper que tiró una excepción; 'http_error' es un corte con progreso parcial."""
    generic = ScrapeResult.failed("boom")
    http_cut = ScrapeResult.finish(raws(1), reached_end=False, error="página 3: HTTP 503", error_reason=bs.HTTP_ERROR)
    assert (generic.incomplete_reason, http_cut.incomplete_reason) == ("error", "http_error")


@pytest.mark.parametrize("status, expected", [(500, True), (502, True), (503, True), (429, True), (408, True),
                                              (400, False), (401, False), (403, False), (404, False)])
def test_is_retryable_por_status(status, expected):
    from backend.scrapers.tecnocasa_scraper import _is_retryable
    assert _is_retryable(requests.HTTPError("x", response=FakeResp(status=status))) is expected


def test_is_retryable_errores_sin_respuesta():
    from backend.scrapers.tecnocasa_scraper import _is_retryable
    assert _is_retryable(requests.ConnectionError("x")) and _is_retryable(requests.Timeout("x"))
    assert _is_retryable(requests.exceptions.JSONDecodeError("x", "doc", 0))


# --- redpiso / remax ---

def redpiso_card(n, madrid=True):
    town = "madrid" if madrid else "las-rozas"
    return (f'<a href="/inmueble/piso-{town}-RP{n}"><h3>Chamberí, Madrid</h3>'
            f'<p class="text-red-500">250.000 €</p><span><i class="fa-bed"></i> 2</span>'
            f'<span><i class="fa-angle-90"></i> 70 m²</span></a>')


def redpiso_page(cards, total=None):
    h1 = f"<h1>{total} viviendas en venta en Madrid</h1>" if total else ""
    return f"<html><body>{h1}{''.join(cards)}</body></html>"


def run_redpiso(http, pages, total="20"):
    from backend.scrapers.redpiso_scraper import BASE_URL, RedpisoScraper

    def handler(url, params):
        n = int(url.split("page=")[1]) if "page=" in url else 1
        return FakeResp(text=pages(n) if callable(pages) else pages.get(n, redpiso_page([], total)))
    session = http(handler)
    assert BASE_URL
    return RedpisoScraper()._fetch_sync(), session


def test_redpiso_pagina_repetida_es_el_bug_actual(http):
    """El sitio ignora ?page: siempre devuelve la misma página. Antes se pedía 10 veces."""
    same = redpiso_page([redpiso_card(i) for i in range(10)], total="1.342")
    result, session = run_redpiso(http, lambda n: same)
    assert (result.complete, result.incomplete_reason) == (False, "repeated_page")
    assert len(result.listings) == 10 and result.total_reported == 1342
    assert len(session.calls) == 2                       # página 1 y la 2, que repite → corta


def test_redpiso_barrido_completo_con_filtrados(http):
    p1 = redpiso_page([redpiso_card(i) for i in range(8)] + [redpiso_card(90, False), redpiso_card(91, False)], "20")
    p2 = redpiso_page([redpiso_card(i) for i in range(10, 18)] + [redpiso_card(92, False), redpiso_card(93, False)], "20")
    result, _ = run_redpiso(http, {1: p1, 2: p2})        # la 3 viene sin tarjetas: fin del catálogo
    assert len(result.listings) == 16 and result.items_seen == 20
    assert (result.complete, result.total_reported) == (True, 20)


def test_redpiso_fin_con_cobertura_baja(http):
    p1 = redpiso_page([redpiso_card(i) for i in range(10)], "1.342")
    result, _ = run_redpiso(http, {1: p1})               # termina en la página 2 vacía, pero vio 10 de 1.342
    assert (result.complete, result.incomplete_reason) == (False, "low_coverage")


def test_redpiso_pagina_con_items_ilegibles_no_es_fin(http):
    """Hay tarjetas pero ninguna se puede leer (cambió el HTML): no es el fin del catálogo."""
    broken = '<html><body><a href="/inmueble/piso-madrid-RP1"><h3>x</h3></a></body></html>'    # sin precio
    result, _ = run_redpiso(http, {1: broken})
    assert (result.complete, result.incomplete_reason) == (False, "unparseable_page")


def test_redpiso_error_http(http):
    from backend.scrapers.redpiso_scraper import RedpisoScraper
    http(lambda url, params: FakeResp(status=503))
    result = RedpisoScraper()._fetch_sync()
    assert (result.complete, result.incomplete_reason) == (False, "error")
    assert "página 1" in result.error


def remax_card(n):
    return (f'<div class="listingRow"><a class="enlace_{n}" href="/inmueble/{n}"></a>'
            f'<div class="inmueble-detalle-precio">300.000 €</div>'
            f'<div class="inmueble-detalle-nombre">Piso en venta, Chamberí - Arapiles, Madrid</div>'
            f'<div class="inmueble-detalle-datos">3 hab</div>'
            f'<div class="inmueble-detalle-datos">90 m<sup>2</sup></div></div>')


def test_remax_pagina_repetida_con_total_informado(http):
    from backend.scrapers.remax_scraper import RemaxScraper
    html = ("<html><body>" + "".join(remax_card(i) for i in range(20))
            + "<span>Propiedades encontradas: 142</span></body></html>")
    session = http(lambda url, params: FakeResp(text=html))
    result = RemaxScraper()._fetch_sync()
    assert (result.complete, result.incomplete_reason) == (False, "repeated_page")
    assert (len(result.listings), result.total_reported) == (20, 142)
    assert len(session.calls) == 2


def test_remax_barrido_completo(http):
    from backend.scrapers.remax_scraper import RemaxScraper
    pages = {0: [remax_card(i) for i in range(20)], 20: [remax_card(i) for i in range(20, 25)]}

    def handler(url, params):
        start = int(url.split("start=")[1]) if "start=" in url else 0
        cards = pages.get(start, [])
        return FakeResp(text="<html><body>" + "".join(cards) + "<span>Propiedades encontradas: 25</span></body></html>")
    http(handler)
    result = RemaxScraper()._fetch_sync()
    assert len(result.listings) == 25
    assert (result.complete, result.total_reported) == (True, 25)


# --- donpiso / wallapop ---

def test_donpiso_no_pagina_asi_que_nunca_es_completo():
    from backend.scrapers.donpiso_scraper import DonpisoScraper
    result = DonpisoScraper()._build_result("<html><body><p>sin tarjetas</p></body></html>")
    assert (result.complete, result.incomplete_reason) == (False, "no_pagination")


def test_run_imprime_el_estado_del_barrido(capsys):
    class S(bs.BaseScraper):
        async def fetch_listings(self):
            return ScrapeResult.finish(raws(1, 2), reached_end=False, stop_reason=bs.PAGE_CAP, total_reported=900)

    result = asyncio.run(S("fake").run())
    assert result.complete is False
    assert "incompleto (page_cap)" in capsys.readouterr().out


# ── scrape_runs: modelo, migración, registro ───────────────────────────────────

REV, PREV = "c4d8f1a7e3b2", "b7e2d9a4c1f0"


def _render(direction):
    """SQL de la migración en dialecto Postgres, sin importar contra qué DB corran los tests."""
    from unittest.mock import patch
    from alembic import command
    from alembic.config import Config
    buf = io.StringIO()
    cfg = Config("alembic.ini", stdout=buf, output_buffer=buf)
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql://x@localhost/render_only"}):
        getattr(command, direction)(cfg, f"{PREV}:{REV}" if direction == "upgrade" else f"{REV}:{PREV}", sql=True)
    return buf.getvalue()


def test_migracion_scrape_runs_sql():
    up, down = _render("upgrade"), _render("downgrade")
    assert "CREATE TABLE scrape_runs" in up
    for col in ("source VARCHAR(50) NOT NULL", "created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL",
                "seen_count INTEGER NOT NULL", "new_count INTEGER NOT NULL", "complete BOOLEAN NOT NULL",
                "total_reported INTEGER", "incomplete_reason VARCHAR(30)", "error TEXT"):
        assert col in up
    assert "CREATE INDEX ix_scrape_runs_source_created_at ON scrape_runs (source, created_at)" in up
    assert "DROP TABLE scrape_runs" in down


def test_migracion_scrape_runs_es_la_cabeza():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [REV]


def test_modelo_scrape_run_coincide_con_la_migracion():
    from backend.models.scrape_run import ScrapeRun
    t = ScrapeRun.__table__
    assert set(t.c.keys()) == {"id", "source", "created_at", "seen_count", "new_count", "complete",
                               "total_reported", "incomplete_reason", "error"}
    assert {c.name for c in t.c if c.nullable} == {"total_reported", "incomplete_reason", "error"}
    assert [c.name for c in next(iter(t.indexes)).columns] == ["source", "created_at"]


class RecordingDB(FakeDB):
    def __init__(self, fail=False, **kw):
        super().__init__(**kw)
        self.added, self.fail = [], fail

    def add(self, obj):
        if self.fail:
            raise RuntimeError("relation scrape_runs does not exist")
        self.added.append(obj)


def test_record_scrape_run_guarda_los_campos():
    from backend.models.repository import record_scrape_run
    result = ScrapeResult.finish(raws(1, 2, 3), reached_end=False, stop_reason=bs.PAGE_CAP, total_reported=926)
    db = RecordingDB()
    record_scrape_run(db, "tecnocasa", result, new_count=2)
    (row,) = db.added
    assert (row.source, row.seen_count, row.new_count, row.complete) == ("tecnocasa", 3, 2, False)
    assert (row.total_reported, row.incomplete_reason, row.error) == (926, "page_cap", None)
    assert db.commits == 1


def test_record_scrape_run_guarda_el_error_truncado():
    from backend.models.repository import record_scrape_run
    db = RecordingDB()
    record_scrape_run(db, "donpiso", ScrapeResult.failed("x" * 5000), 0)
    assert len(db.added[0].error) == 1000 and db.added[0].incomplete_reason == "error"


def test_record_scrape_run_nunca_rompe_el_scraping():
    """Por ejemplo, si el código se despliega antes de aplicar la migración."""
    from backend.models.repository import record_scrape_run
    db = RecordingDB(fail=True)
    record_scrape_run(db, "remax", ScrapeResult.finish(raws(1), reached_end=True), 1)   # no tira
    assert db.rollbacks == 1 and db.commits == 0


# ── nodo scrape del pipeline ───────────────────────────────────────────────────

@pytest.fixture
def scrape_node(monkeypatch):
    from contextlib import contextmanager
    from backend.pipeline import nodes
    recorded, saved = [], []

    @contextmanager
    def fake_session():
        yield FakeDB()

    monkeypatch.setattr(nodes, "_session", fake_session)
    monkeypatch.setattr(nodes, "save_listing",
                        lambda db, r: (SimpleNamespace(id=len(saved) + 1), saved.append(r) or True))
    monkeypatch.setattr(nodes, "record_scrape_run",
                        lambda db, source, result, new: recorded.append((source, result.complete, result.incomplete_reason,
                                                                         result.error, new)))
    return nodes, recorded


def scraper_returning(result=None, exc=None):
    class S:
        async def run(self):
            if exc:
                raise exc
            return result
    return S


def test_nodo_scrape_registra_cada_fuente_y_expone_complete(scrape_node):
    nodes, recorded = scrape_node
    scrapers = {
        "tecnocasa": scraper_returning(ScrapeResult.finish(raws(1, 2), reached_end=True, total_reported=2)),
        "redpiso": scraper_returning(ScrapeResult.finish(raws(3), reached_end=False, stop_reason=bs.REPEATED_PAGE,
                                                         total_reported=1342)),
        "donpiso": scraper_returning(exc=RuntimeError("Chromium no instalado")),
    }
    out = asyncio.run(nodes.scrape({"sources": ["tecnocasa", "redpiso", "donpiso"]},
                                   {"configurable": {"scrapers": scrapers}}))

    assert out["source_stats"] == {
        "tecnocasa": {"new": 2, "dup": 0, "found": 2, "complete": True, "total_reported": 2, "incomplete_reason": None},
        "redpiso": {"new": 1, "dup": 0, "found": 1, "complete": False, "total_reported": 1342,
                    "incomplete_reason": "repeated_page"},
    }
    assert recorded == [("tecnocasa", True, None, None, 2),
                        ("redpiso", False, "repeated_page", None, 1),
                        ("donpiso", False, "error", "RuntimeError: Chromium no instalado", 0)]   # la que falló también queda registrada
    assert [(e["node"], e["source"]) for e in out["errors"]] == [("scrape", "donpiso")]
    assert nodes.complete_sources({"source_stats": out["source_stats"]}) == ["tecnocasa"]


def test_nodo_scrape_corte_por_error_http_queda_en_errors(scrape_node):
    nodes, recorded = scrape_node
    partial = ScrapeResult.finish(raws(1, 2, 3), reached_end=False, error="página 3: HTTP 500")
    out = asyncio.run(nodes.scrape({"sources": ["tecnocasa"]},
                                   {"configurable": {"scrapers": {"tecnocasa": scraper_returning(partial)}}}))
    assert out["source_stats"]["tecnocasa"]["complete"] is False
    assert out["source_stats"]["tecnocasa"]["incomplete_reason"] == "error"
    assert [(e["source"], e["error"]) for e in out["errors"]] == [("tecnocasa", "página 3: HTTP 500")]
    assert out["new_ids"] == [1, 2, 3]                  # lo que alcanzó a ver se guarda igual


# ── Contra DB real (SQLite local / Postgres en CI) ─────────────────────────────

SRC_FULL, SRC_PART = "pytest-c3-full", "pytest-c3-part"


@pytest.fixture
def clean_c3():
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    from backend.models.scrape_run import ScrapeRun

    def wipe():
        db = SessionLocal()
        db.query(Listing).filter(Listing.source.in_([SRC_FULL, SRC_PART])).delete()
        db.query(ScrapeRun).filter(ScrapeRun.source.in_([SRC_FULL, SRC_PART])).delete()
        db.commit()
        db.close()

    wipe()
    yield
    wipe()


@requires_test_db
def test_db_record_scrape_run(clean_c3):
    from backend.models.database import SessionLocal
    from backend.models.repository import record_scrape_run
    from backend.models.scrape_run import ScrapeRun
    db = SessionLocal()
    try:
        record_scrape_run(db, SRC_PART, ScrapeResult.finish(raws(1, 2), reached_end=False,
                                                            stop_reason=bs.PAGE_CAP, total_reported=900), 1)
        row = db.query(ScrapeRun).filter_by(source=SRC_PART).one()
        assert (row.seen_count, row.new_count, row.complete, row.total_reported, row.incomplete_reason) == \
            (2, 1, False, 900, "page_cap")
        assert datetime.utcnow() - row.created_at < timedelta(minutes=1)
    finally:
        db.close()


@requires_test_db
def test_db_pipeline_solo_desactiva_la_fuente_con_barrido_completo(clean_c3):
    """Dos fuentes con un listing viejo (40 días) cada una. La completa lo desactiva; la
    incompleta, aunque trajo listings, no toca nada. Ambos barridos quedan en scrape_runs."""
    from langgraph.checkpoint.memory import InMemorySaver
    from backend.models.database import SessionLocal
    from backend.models.listing import Listing
    from backend.models.scrape_run import ScrapeRun
    from backend.pipeline.graph import run_pipeline

    old = datetime.utcnow() - timedelta(days=40)
    db = SessionLocal()
    for src in (SRC_FULL, SRC_PART):
        db.add(Listing(source=src, external_id="viejo", url="u", title="t", price=200000.0,
                       last_seen_at=old, is_active=True))
    db.commit()
    db.close()

    def make(src, complete):
        class S:
            async def run(self):
                found = [raw(1, source=src)]
                if complete:
                    return ScrapeResult.finish(found, reached_end=True, total_reported=1)
                return ScrapeResult.finish(found, reached_end=False, stop_reason=bs.PAGE_CAP, total_reported=900)
        return S

    async def no_page(url):
        return None

    state = asyncio.run(run_pipeline(
        [SRC_FULL, SRC_PART], checkpointer=InMemorySaver(), fetch_html=no_page, max_llm_calls=0,
        scrapers={SRC_FULL: make(SRC_FULL, True), SRC_PART: make(SRC_PART, False)}))

    db = SessionLocal()
    try:
        active = {(x.source, x.external_id): x.is_active
                  for x in db.query(Listing).filter(Listing.source.in_([SRC_FULL, SRC_PART]))}
        runs = {r.source: r for r in db.query(ScrapeRun).filter(ScrapeRun.source.in_([SRC_FULL, SRC_PART]))}
    finally:
        db.close()

    assert active[(SRC_FULL, "viejo")] is False         # barrido completo: lo no visto se desactiva
    assert active[(SRC_PART, "viejo")] is True          # incompleto: no se toca
    assert state["deactivated_count"] == 1
    assert runs[SRC_FULL].complete is True and runs[SRC_PART].complete is False
    assert (runs[SRC_PART].incomplete_reason, runs[SRC_PART].total_reported) == ("page_cap", 900)
