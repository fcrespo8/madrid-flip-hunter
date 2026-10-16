"""WallapopScraper: user_agent normal, bloqueo vs "no hubo API", y paginación con meta.next_page.
Offline: una página y una API falsas imitan lo medido contra el sitio real (2026-10)."""
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from backend.scrapers import wallapop_scraper as ws
from backend.scrapers.wallapop_scraper import WallapopScraper

FIRST_URL = f"{ws.API_URL}?keywords=piso+en+venta+madrid&source=deep_link&distance_in_km=15&category_id=200"
# Headers de la primera llamada de la app: solo los de API_HEADERS deben reenviarse.
FIRST_HEADERS = {":authority": "api.wallapop.com", ":method": "GET", "user-agent": "Mozilla/5.0 ...",
                 "origin": "https://es.wallapop.com", "referer": "https://es.wallapop.com/",
                 "sec-ch-ua": '"Chromium";v="148"', "accept": "application/json, text/plain, */*",
                 "accept-language": "es-ES", "deviceos": "0", "mpid": "-123", "trackinguserid": "u-1",
                 "x-appversion": "89340", "x-deviceid": "dev-1", "x-deviceos": "0"}
EXPECTED_FORWARDED = {k: v for k, v in FIRST_HEADERS.items() if k in ws.API_HEADERS}


def item(n, title="Piso en venta en Chueca en Madrid", surface=None, description="reformar"):
    type_attributes = {"operation": "Venta", "type": "Piso", "rooms": 2, "bathrooms": 1}
    if surface is not None:
        type_attributes["surface"] = surface
    return {"id": f"w{n}", "title": title, "description": description, "web_slug": f"piso-{n}",
            "price": {"amount": 250000.0, "currency": "EUR"}, "location": {"latitude": 40.4, "longitude": -3.7},
            "type_attributes": type_attributes}


class FakeApi:
    """Catálogo de len(pages) páginas; pages[k-1] = ids de la página k. La página k>=2 se pide con
    ?next_page=tok{k}. `end` es cómo marca la última página: null | empty-string | missing-key | no-meta.
    failures: {página: (status | Exception | "badjson", veces)}."""

    def __init__(self, pages, end="null", failures=None, first_status=200, first_body=None, surfaces=None,
                 descriptions=None):
        self.pages, self.end, self.failures = pages, end, failures or {}
        self.surfaces = surfaces or {}                                  # {id: surface}
        self.descriptions = descriptions or {}                          # {id: descripción}
        self.first_status, self.first_body = first_status, first_body
        self.hits = {}
        self.first_enabled = True

    def body(self, k):
        last = k == len(self.pages)
        body = {"data": {"section": {"items": [item(int(i), surface=self.surfaces.get(i), description=self.descriptions.get(i, "reformar"))
                                  for i in self.pages[k - 1]], "title": "x"}}}
        if last and self.end == "no-meta":
            return body
        token = {"null": None, "empty-string": "", "missing-key": ...}[self.end] if last else f"tok{k + 1}"
        body["meta"] = {} if token is ... else {"next_page": token}
        return body

    def first_response(self):
        body = self.first_body if self.first_body is not None else self.body(1)
        return SimpleNamespace(
            url=FIRST_URL, status=self.first_status,
            request=SimpleNamespace(all_headers=_async(FIRST_HEADERS)),
            json=_async(body) if not isinstance(body, Exception) else _raiser(body))

    def fetch(self, url):
        k = int(parse_qs(urlparse(url).query)["next_page"][0].removeprefix("tok"))
        self.hits[k] = self.hits.get(k, 0) + 1
        code, times = self.failures.get(k, (None, 0))
        if code is not None and self.hits[k] <= times:
            if isinstance(code, Exception):
                raise code
            if code == "badjson":
                return {"status": 200, "text": "<html>gateway</html>"}
            return {"status": code, "text": ""}
        return {"status": 200, "text": json.dumps(self.body(k))}


def _async(value):
    async def f():
        return value
    return f


def _raiser(exc):
    async def f():
        raise exc
    return f


class FakePage:
    def __init__(self, api, nav=((200, {}),), title="Wallapop"):
        self.api, self.nav, self.title_text = api, list(nav), title
        self.handlers, self.gotos, self.fetches, self.routes = [], 0, [], []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def on(self, event, handler):
        assert event == "response"
        self.handlers.append(handler)

    async def goto(self, url, wait_until=None):
        assert url == WallapopScraper.SEARCH_URL and wait_until == "networkidle"
        self.gotos += 1
        step = self.nav.pop(0) if len(self.nav) > 1 else self.nav[0]       # la última respuesta se repite
        if isinstance(step, Exception):
            raise step
        status, headers = step
        if status < 400 and self.api.first_enabled:
            for handler in self.handlers:
                await handler(self.api.first_response())
        return SimpleNamespace(status=status, headers=headers)

    async def title(self):
        return self.title_text

    async def evaluate(self, script, arg):
        self.fetches.append(arg)
        return self.api.fetch(arg["url"])


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


def crawl(page):
    return asyncio.run(WallapopScraper()._crawl(page))


def ids_range(start, stop):
    return list(range(start, stop))


THREE_PAGES = [ids_range(1, 41), ids_range(41, 63), ids_range(63, 70)]       # 40 + 22 + 7


# ── 1. user_agent normal (el fix del 403) ──────────────────────────────────────

class FakeBrowser:
    def __init__(self, page):
        self.page, self.context_kwargs, self.closed = page, None, False

    async def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return SimpleNamespace(new_page=_async(self.page))

    async def close(self):
        self.closed = True


def install_fake_playwright(monkeypatch, browser):
    launched = {}

    class Chromium:
        async def launch(self, **kwargs):
            launched.update(kwargs)
            return browser

    class CM:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *exc):
            return False

    class FakeStealth:
        async def apply_stealth_async(self, page):
            launched["stealth_on"] = page

    monkeypatch.setattr(ws, "async_playwright", lambda: CM())
    monkeypatch.setattr(ws, "Stealth", FakeStealth)
    return launched


def test_el_contexto_usa_un_user_agent_normal(monkeypatch, sleeps):
    page = FakePage(FakeApi([[1, 2, 3]]))
    browser = FakeBrowser(page)
    launched = install_fake_playwright(monkeypatch, browser)

    result = asyncio.run(WallapopScraper().fetch_listings())

    kwargs = browser.context_kwargs
    assert kwargs["locale"] == "es-ES"
    assert kwargs["user_agent"] == ws.USER_AGENT
    assert "headless" not in kwargs["user_agent"].lower()          # "HeadlessChrome" en el header → 403 de CloudFront
    assert kwargs["user_agent"].startswith("Mozilla/5.0") and "Chrome/" in kwargs["user_agent"]
    assert launched["headless"] is True and launched["stealth_on"] is page
    assert browser.closed is True
    assert (result.complete, len(result.listings)) == (True, 3)


def test_el_navegador_se_cierra_aunque_el_recorrido_falle(monkeypatch):
    browser = FakeBrowser(FakePage(FakeApi([[1]])))
    install_fake_playwright(monkeypatch, browser)

    async def boom(self, page):
        raise RuntimeError("explotó")
    monkeypatch.setattr(WallapopScraper, "_crawl", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(WallapopScraper().fetch_listings())
    assert browser.closed is True


# ── 2. Bloqueo vs "no hubo API" ────────────────────────────────────────────────

def test_403_en_la_primera_respuesta_es_blocked(sleeps):
    page = FakePage(FakeApi([[1]]), nav=[(403, {"server": "CloudFront"})], title="ERROR: The request could not be satisfied")
    result = crawl(page)
    assert (result.complete, result.incomplete_reason, result.listings) == (False, "blocked", [])
    assert result.error == ("HTTP 403 en la primera respuesta (server: CloudFront, "
                            "'ERROR: The request could not be satisfied') (tras 1 intento)")
    assert page.gotos == 1 and page.fetches == [] and sleeps == []          # un 403 no se reintenta


def test_429_se_reintenta_y_si_persiste_es_blocked(sleeps):
    page = FakePage(FakeApi([[1]]), nav=[(429, {})])
    result = crawl(page)
    assert (page.gotos, sleeps) == (3, [2.0, 4.0])
    assert (result.incomplete_reason, "tras 3 intentos" in result.error) == ("blocked", True)


def test_un_5xx_transitorio_se_recupera(sleeps):
    page = FakePage(FakeApi([[1, 2]]), nav=[(503, {}), (200, {})])
    result = crawl(page)
    assert (page.gotos, sleeps) == (2, [2.0])
    assert (result.complete, len(result.listings)) == (True, 2)


def test_5xx_persistente_es_http_error(sleeps):
    result = crawl(FakePage(FakeApi([[1]]), nav=[(502, {})]))
    assert (result.complete, result.incomplete_reason) == (False, "http_error")
    assert result.error.startswith("HTTP 502 en la primera respuesta") and result.error.endswith("(tras 3 intentos)")


def test_un_timeout_de_navegacion_se_reintenta_y_es_http_error(sleeps):
    page = FakePage(FakeApi([[1]]), nav=[TimeoutError("networkidle excedido")])
    result = crawl(page)
    assert (page.gotos, sleeps) == (3, [2.0, 4.0])
    assert result.incomplete_reason == "http_error"
    assert "navegación fallida: TimeoutError: networkidle excedido (tras 3 intentos)" == result.error


def test_pagina_cargada_sin_llamada_a_la_api_es_no_api(sleeps):
    api = FakeApi([[1]])
    api.first_enabled = False
    result = crawl(FakePage(api, title="Wallapop: Gana dinero"))
    assert (result.complete, result.incomplete_reason, result.listings) == (False, "no_api", [])
    assert result.error == "la página cargó (HTTP 200, 'Wallapop: Gana dinero') pero no hubo llamada a search/section"
    assert sleeps == [0.5] * 20                                              # espera hasta 10 s, sondeando cada 0.5


def test_blocked_y_no_api_son_motivos_distintos(sleeps):
    blocked = crawl(FakePage(FakeApi([[1]]), nav=[(403, {})]))
    api = FakeApi([[1]])
    api.first_enabled = False
    no_api = crawl(FakePage(api))
    assert {blocked.incomplete_reason, no_api.incomplete_reason} == {"blocked", "no_api"}


@pytest.mark.parametrize("status, reason", [(403, "blocked"), (401, "blocked"), (429, "blocked"), (500, "http_error"), (404, "http_error")])
def test_la_primera_llamada_a_la_api_con_error(sleeps, status, reason):
    result = crawl(FakePage(FakeApi([[1]], first_status=status)))
    assert (result.complete, result.incomplete_reason) == (False, reason)
    assert result.error == f"search/section respondió HTTP {status} en la primera llamada"


@pytest.mark.parametrize("first_body", [{"data": {}, "meta": {"next_page": None}}, {"meta": {}}, ["no", "es", "dict"],
                                        ValueError("cuerpo no es JSON")])
def test_formato_inesperado_en_la_primera_respuesta(sleeps, first_body):
    result = crawl(FakePage(FakeApi([[1]], first_body=first_body)))
    assert (result.complete, result.incomplete_reason, result.listings) == (False, "unparseable_page", [])


# ── 3. Paginación con meta.next_page ───────────────────────────────────────────

def test_recorre_hasta_que_no_hay_next_page(sleeps):
    page = FakePage(FakeApi(THREE_PAGES))
    result = crawl(page)
    assert (result.complete, result.incomplete_reason, result.error) == (True, None, None)
    assert (len(result.listings), result.items_seen, result.total_reported) == (69, 69, None)
    assert [f["url"] for f in page.fetches] == [f"{ws.API_URL}?next_page=tok2", f"{ws.API_URL}?next_page=tok3"]
    assert sleeps == [1.0, 1.0]                                              # 1 s entre páginas, ninguna tras la última


def test_las_paginas_siguientes_reenvian_solo_los_headers_de_la_app(sleeps):
    page = FakePage(FakeApi(THREE_PAGES))
    crawl(page)
    assert all(f["headers"] == EXPECTED_FORWARDED for f in page.fetches)
    assert {"x-deviceid", "mpid", "x-appversion"} <= set(EXPECTED_FORWARDED)
    assert not {":authority", "user-agent", "origin", "referer", "sec-ch-ua"} & set(page.fetches[0]["headers"])


def test_el_token_next_page_se_escapa_en_la_url(sleeps):
    """El token real es un JWT (solo caracteres seguros), pero se escapa igual: un token con
    espacios, '/', '+' o '=' no debe romper ni inyectar parámetros en la URL."""
    api = FakeApi([[1], [2]])
    first = api.body(1)
    first["meta"]["next_page"] = "a b/c+d==&x=1"
    api.first_body = first
    requested = []

    def fetch(url):
        requested.append(url)
        return {"status": 200, "text": json.dumps(api.body(2))}
    api.fetch = fetch

    result = crawl(FakePage(api))
    assert requested == [f"{ws.API_URL}?next_page=a%20b%2Fc%2Bd%3D%3D%26x%3D1"]
    assert (result.complete, len(result.listings)) == (True, 2)


def test_catalogo_de_una_sola_pagina(sleeps):
    page = FakePage(FakeApi([ids_range(1, 6)]))
    result = crawl(page)
    assert (result.complete, len(result.listings), page.fetches, sleeps) == (True, 5, [], [])


@pytest.mark.parametrize("end", ["null", "empty-string", "missing-key"])
def test_las_formas_de_marcar_la_ultima_pagina(sleeps, end):
    """La API real cierra con meta: {"next_page": null}; las otras formas se aceptan igual."""
    result = crawl(FakePage(FakeApi(THREE_PAGES, end=end)))
    assert (result.complete, len(result.listings)) == (True, 69)


def test_sin_bloque_meta_no_se_asume_que_termino(sleeps):
    """Si un cambio del sitio saca `meta`, no hay forma de saber si hay más: incompleto."""
    result = crawl(FakePage(FakeApi(THREE_PAGES, end="no-meta")))
    assert (result.complete, result.incomplete_reason) == (False, "bad_pagination")
    assert len(result.listings) == 69                                         # lo visto se conserva


def test_una_ultima_pagina_vacia_es_el_fin_no_un_error(sleeps):
    result = crawl(FakePage(FakeApi([ids_range(1, 4), []])))
    assert (result.complete, len(result.listings)) == (True, 3)


def test_pagina_sin_anuncios_nuevos_pero_con_next_page_corta(sleeps):
    page = FakePage(FakeApi([[1, 2, 3], [1, 2, 3], [4, 5]]))                 # la 2 repite la 1 y promete una 3
    result = crawl(page)
    assert (result.complete, result.incomplete_reason) == (False, "repeated_page")
    assert (len(result.listings), len(page.fetches)) == (3, 1)


def test_pagina_vacia_con_next_page_corta(sleeps):
    page = FakePage(FakeApi([[1, 2, 3], [], [4]]))
    result = crawl(page)
    assert (result.complete, result.incomplete_reason, len(page.fetches)) == (False, "repeated_page", 1)


def test_paginas_con_solapamiento_parcial_no_duplican(sleeps):
    result = crawl(FakePage(FakeApi([[1, 2, 3], [3, 4, 5]])))
    assert (result.complete, [x.external_id for x in result.listings]) == (True, ["w1", "w2", "w3", "w4", "w5"])
    assert result.items_seen == 5


def test_techo_de_seguridad(sleeps, monkeypatch):
    monkeypatch.setattr(ws, "MAX_PAGES_SAFETY", 3)
    page = FakePage(FakeApi([[i * 10 + j for j in range(5)] for i in range(10)]))      # 10 páginas
    result = crawl(page)
    assert (len(page.fetches), len(result.listings)) == (2, 15)
    assert (result.complete, result.incomplete_reason) == (False, "page_cap")


# ── 4. Errores y reintentos a mitad del recorrido ──────────────────────────────

def test_reintenta_un_fallo_transitorio_y_sigue(sleeps):
    page = FakePage(FakeApi(THREE_PAGES, failures={2: (503, 1)}))
    result = crawl(page)
    assert api_hits(page, 2) == 2 and result.complete is True and len(result.listings) == 69
    assert sleeps == [1.0, 2.0, 1.0]                                          # pausa, backoff de 2 s, pausa


def api_hits(page, k):
    return page.api.hits[k]


def test_corta_tras_agotar_los_reintentos_con_http_error(sleeps):
    page = FakePage(FakeApi(THREE_PAGES, failures={3: (503, 99)}))
    result = crawl(page)
    assert api_hits(page, 3) == 3                                             # 1 intento + 2 reintentos
    assert sleeps == [1.0, 1.0, 2.0, 4.0]
    assert (result.complete, result.incomplete_reason) == (False, "http_error")
    assert result.error == "página 3: HTTP 503 (tras 3 intentos)"
    assert len(result.listings) == 62                                         # las páginas 1 y 2 se conservan


def test_403_a_mitad_es_blocked_y_no_se_reintenta(sleeps):
    page = FakePage(FakeApi(THREE_PAGES, failures={2: (403, 99)}))
    result = crawl(page)
    assert (api_hits(page, 2), sleeps) == (1, [1.0])
    assert (result.incomplete_reason, result.error) == ("blocked", "página 2: HTTP 403 (tras 1 intento)")
    assert len(result.listings) == 40


def test_429_a_mitad_se_reintenta_y_si_persiste_es_blocked(sleeps):
    page = FakePage(FakeApi(THREE_PAGES, failures={2: (429, 99)}))
    result = crawl(page)
    assert (api_hits(page, 2), result.incomplete_reason) == (3, "blocked")


@pytest.mark.parametrize("status", [400, 404])
def test_otros_4xx_a_mitad_son_http_error_sin_reintento(sleeps, status):
    page = FakePage(FakeApi(THREE_PAGES, failures={2: (status, 99)}))
    result = crawl(page)
    assert (api_hits(page, 2), result.incomplete_reason) == (1, "http_error")


def test_un_fallo_de_red_dentro_de_la_pagina_se_reintenta(sleeps):
    page = FakePage(FakeApi(THREE_PAGES, failures={2: (RuntimeError("TypeError: Failed to fetch"), 2)}))
    result = crawl(page)
    assert (api_hits(page, 2), result.complete) == (3, True)
    assert sleeps == [1.0, 2.0, 4.0, 1.0]


def test_un_cuerpo_que_no_es_json_se_reintenta(sleeps):
    page = FakePage(FakeApi(THREE_PAGES, failures={2: ("badjson", 1)}))
    result = crawl(page)
    assert (api_hits(page, 2), result.complete) == (2, True)


def test_un_cuerpo_que_nunca_es_json_corta_con_http_error(sleeps):
    page = FakePage(FakeApi(THREE_PAGES, failures={2: ("badjson", 99)}))
    result = crawl(page)
    assert (result.incomplete_reason, result.error) == ("http_error", "página 2: la respuesta no es JSON (tras 3 intentos)")


def test_formato_inesperado_a_mitad_conserva_lo_visto(sleeps):
    api = FakeApi(THREE_PAGES)
    original = api.fetch

    def fetch(url):
        out = original(url)
        if url.endswith("tok3"):
            out["text"] = json.dumps({"data": {"section": {}}, "meta": {"next_page": None}})
        return out
    api.fetch = fetch
    result = crawl(FakePage(api))
    assert (result.complete, result.incomplete_reason, len(result.listings)) == (False, "unparseable_page", 62)


# ── 5. Piezas puras ────────────────────────────────────────────────────────────

def test_parse_section():
    page = ws.parse_section({"data": {"section": {"items": [item(1)]}}, "meta": {"next_page": "abc"}})
    assert (len(page.items), page.next_page, page.has_meta) == (1, "abc", True)
    last = ws.parse_section({"data": {"section": {"items": []}}, "meta": {"next_page": None}})
    assert (last.items, last.next_page, last.has_meta) == ([], None, True)
    assert ws.parse_section({"data": {"section": {"items": []}}}).has_meta is False


@pytest.mark.parametrize("body", [None, [], "x", {}, {"data": None}, {"data": {"section": None}},
                                  {"data": {"section": {"items": "no"}}}])
def test_parse_section_con_formato_inesperado(body):
    assert ws.parse_section(body).items is None


@pytest.mark.parametrize("token", ["", None, 5, ["a"]])
def test_parse_section_ignora_tokens_invalidos(token):
    assert ws.parse_section({"data": {"section": {"items": []}}, "meta": {"next_page": token}}).next_page is None


@pytest.mark.parametrize("status, expected", [(408, True), (429, True), (500, True), (502, True), (503, True), (599, True),
                                              (400, False), (401, False), (403, False), (404, False)])
def test_is_retryable_status(status, expected):
    assert ws.is_retryable_status(status) is expected


@pytest.mark.parametrize("status, reason", [(401, "blocked"), (403, "blocked"), (429, "blocked"),
                                            (404, "http_error"), (500, "http_error"), (None, "http_error")])
def test_failure_reason(status, reason):
    assert ws.failure_reason(status) == reason


def test_parse_item_no_cambio():
    listing = WallapopScraper()._parse_item(item(7, title="Piso en venta en Justicia - Chueca en Madrid"))
    assert (listing.source, listing.external_id, listing.price, listing.rooms) == ("wallapop", "w7", 250000.0, 2)
    assert (listing.neighborhood, listing.lat, listing.lon) == ("Justicia - Chueca", 40.4, -3.7)
    assert listing.url == "https://es.wallapop.com/item/piso-7"
    assert WallapopScraper()._parse_item({"title": "sin id"}) is None


# ── 6. Superficie: type_attributes.surface → size_m2 ──────────────────────────

@pytest.mark.parametrize("value, expected", [
    (50.0, 50.0), (62, 62.0), (232.0, 232.0), (41.5, 41.5),
    ("70.5", 70.5), ("70,5", 70.5), (" 55 ", 55.0),                    # texto numérico (la API hoy manda float)
    (None, None), ("", None), ("abc", None), ("55 m2", None),
    (0, None), (0.0, None), (-5, None), ("-5", None),                  # un 0 o negativo no es un tamaño
    (True, None), (False, None),                                       # bool es int en Python: no vale
    (float("nan"), None), (float("inf"), None), ([], None), ({}, None),
])
def test_parse_surface(value, expected):
    assert ws.parse_surface(value) == expected


def test_parse_item_lee_surface_como_size_m2():
    listing = WallapopScraper()._parse_item(item(7, surface=62.0))
    assert (listing.size_m2, listing.rooms, listing.price) == (62.0, 2, 250000.0)
    assert isinstance(listing.size_m2, float)


def test_parse_item_sin_surface_deja_size_m2_en_none():
    """La API la trae en ~1/3 de los anuncios; en el resto queda sin dato (lo completa enrich_sizes)."""
    assert WallapopScraper()._parse_item(item(7)).size_m2 is None


@pytest.mark.parametrize("type_attributes", [None, {}, {"surface": None}, {"surface": 0}, {"surface": "n/d"}])
def test_parse_item_tolera_type_attributes_raros(type_attributes):
    raw_item = item(7)
    raw_item["type_attributes"] = type_attributes
    listing = WallapopScraper()._parse_item(raw_item)
    assert listing is not None and listing.size_m2 is None             # no revienta ni inventa un tamaño


def test_el_recorrido_completo_conserva_la_superficie(sleeps):
    api = FakeApi(THREE_PAGES, surfaces={1: 50.0, 41: 80.0, 63: 120.0})
    result = crawl(FakePage(api))
    by_id = {x.external_id: x.size_m2 for x in result.listings}
    assert (by_id["w1"], by_id["w41"], by_id["w63"]) == (50.0, 80.0, 120.0)
    assert by_id["w2"] is None and sum(v is not None for v in by_id.values()) == 3


def test_un_listing_con_surface_ya_se_puede_puntuar():
    """El motivo del cambio: sin m², pre_score lo marcaba 'unscorable' (no_size)."""
    from backend.agents.pre_scorer import unscorable_reason
    from backend.models.listing import Listing

    def as_listing(raw):
        return Listing(source=raw.source, external_id=raw.external_id, url=raw.url, title=raw.title, price=raw.price,
                       size_m2=raw.size_m2, rooms=raw.rooms, neighborhood=raw.neighborhood, district=raw.district)

    scraper = WallapopScraper()
    con = as_listing(scraper._parse_item(item(1, title="Piso en venta en Chueca en Madrid", surface=62.0)))
    sin = as_listing(scraper._parse_item(item(2, title="Piso en venta en Chueca en Madrid")))
    assert (unscorable_reason(con), unscorable_reason(sin)) == (None, "no_size")


# ── 7. Superficie desde la descripción (cuando no hay type_attributes.surface) ──

@pytest.mark.parametrize("text, expected", [
    # formatos de unidad
    ("Piso de 54 m² construidos", 54.0), ("Piso de 62m2", 62.0), ("62 m2", 62.0), ("62 M2", 62.0),
    ("62 mts2", 62.0), ("62 mt²", 62.0), ("80 metros cuadrados", 80.0), ("80 metro cuadrado", 80.0),
    ("80 m cuadrados", 80.0),
    # decimales y miles
    ("54,5 m²", 54.5), ("103.13 m²", 103.13), ("1.200 m²", None),            # 1.200 = mil doscientos → implausible
    ("Finca de 15.500 m²", None), ("Nave de 20.000 m²", None),                # punto de miles: leído como decimal serían 15,5 y 20 m²
    # varios números: el primero plausible
    ("Piso de 54 m² construidos y 50m2 útiles", 54.0),
    ("Vivienda de 85 m² con salón comedor luminoso", 85.0),                   # "con salón" DESPUÉS del número no excluye
    # espacios accesorios, antes o después del número
    ("Con salón comedor de 30 m² y una superficie de 74 m²", 74.0),
    ("Piso de 70 m² con terraza de 20 m²", 70.0),
    ("20 m² de terraza y piso de 70 m²", 70.0),                               # "piso" de por medio: el 70 es la vivienda
    ("Trastero de 18 m² incluido, vivienda de 60 m²", 60.0),
    ("dormitorio de 15 m², vivienda de 90 m²", 90.0),
    ("Solárium de 95 m² y piso de 60 m²", 60.0),
    ("parcela de 500 m² y casa de 120 m²", 120.0),
    ("salón de 30 m²", None),                                                 # solo hay un espacio accesorio
    # la palabra clave de otra oración no arrastra
    ("Tiene terraza. El piso mide 70 m²", 70.0), ("Tiene terraza; el piso mide 70 m²", 70.0),
    ("Tiene terraza. Mide 70 m²", 70.0), ("Tiene terraza; mide 70 m²", 70.0),    # sin "piso" de por medio: corta solo el signo
    ("Tiene terraza, mide 70 m²", None),                                       # una coma NO corta (misma oración): conservador
    ("Descripción\n\nTerraza\nPiso de 75 m² en Chueca", 75.0),
    # no es superficie
    ("a 300 metros del metro", None), ("a 5 m del metro", None), ("a 12 minutos andando", None),
    ("4.500 €/m²", None), ("3500€/m2", None), ("sin datos de superficie", None),
    # fuera de lo plausible (mismo rango que el QA)
    ("5 m²", None), ("14 m²", None), ("15 m²", 15.0), ("1000 m²", 1000.0), ("1001 m²", None),
    ("Consta de 6 m² y una vivienda de 85 m²", 85.0),                         # el 6 se descarta por implausible
    # Limitación conocida: "patio y 85 m²" se excluye por la palabra clave que lo precede. Es el lado
    # seguro: sin dato (lo completa enrich_sizes) antes que un tamaño falso que distorsione el score.
    ("Consta de 6 m² de patio y 85 m² de vivienda", None),
    # no es texto
    ("", None), (None, None), (123, None), (["54 m²"], None), ({}, None),
])
def test_parse_size_from_description(text, expected):
    assert ws.parse_size_from_description(text) == expected


def test_parse_item_usa_la_descripcion_si_no_hay_surface():
    listing = WallapopScraper()._parse_item(item(7, description="Piso de 54 m² construidos con terraza de 12 m²"))
    assert listing.size_m2 == 54.0 and isinstance(listing.size_m2, float)


def test_surface_manda_sobre_la_descripcion():
    """`surface` es el dato estructurado: aunque el texto cite otra cifra (suele ser la construida)."""
    listing = WallapopScraper()._parse_item(item(7, surface=50.0, description="Piso de 54 m² construidos y 50m2 útiles"))
    assert listing.size_m2 == 50.0


@pytest.mark.parametrize("surface", [0, 0.0, -3, "n/d", None, True])
def test_una_surface_invalida_cae_a_la_descripcion(surface):
    raw_item = item(7, description="Vivienda de 60 m² reformada")
    raw_item["type_attributes"]["surface"] = surface
    assert WallapopScraper()._parse_item(raw_item).size_m2 == 60.0


def test_sin_surface_ni_m2_en_el_texto_queda_en_none():
    scraper = WallapopScraper()
    assert scraper._parse_item(item(7, description="Piso luminoso para reformar")).size_m2 is None
    assert scraper._parse_item(item(8, description="")).size_m2 is None
    no_description = item(9)
    del no_description["description"]
    assert scraper._parse_item(no_description).size_m2 is None


def test_el_recorrido_completo_lee_la_descripcion(sleeps):
    api = FakeApi(THREE_PAGES, surfaces={1: 50.0},
                  descriptions={1: "Piso de 54 m²", 2: "Vivienda de 85 m² con salón", 3: "terraza de 20 m²", 41: "Piso de 70 m²"})
    result = crawl(FakePage(api))
    by_id = {x.external_id: x.size_m2 for x in result.listings}
    assert (by_id["w1"], by_id["w2"], by_id["w3"], by_id["w41"]) == (50.0, 85.0, None, 70.0)   # surface > texto > nada
    assert by_id["w4"] is None                                                                  # "reformar": sin dato


def test_un_listing_con_m2_en_la_descripcion_ya_se_puede_puntuar():
    from backend.agents.pre_scorer import unscorable_reason
    from backend.models.listing import Listing

    def as_listing(raw):
        return Listing(source=raw.source, external_id=raw.external_id, url=raw.url, title=raw.title, price=raw.price,
                       size_m2=raw.size_m2, rooms=raw.rooms, neighborhood=raw.neighborhood, district=raw.district)

    scraper = WallapopScraper()
    con = as_listing(scraper._parse_item(item(1, description="Piso de 62 m² para reformar")))
    sin = as_listing(scraper._parse_item(item(2, description="Piso para reformar")))
    assert (unscorable_reason(con), unscorable_reason(sin)) == (None, "no_size")


# ── 5. Coordenadas fijas de Madrid en el pedido ────────────────────────────────

APP_URL = (f"{ws.API_URL}?keywords=piso+en+venta+madrid&source=deep_link&distance_in_km=15&category_id=200"
           "&search_id=abc&latitude=40.4252&longitude=-3.6927&order_by=most_relevance&search_country=ES")


def test_la_busqueda_usa_las_mismas_coordenadas_que_se_fijan():
    query = parse_qs(urlparse(WallapopScraper.SEARCH_URL).query)
    assert (float(query["latitude"][0]), float(query["longitude"][0])) == (ws.MADRID_LAT, ws.MADRID_LON)


def test_pin_coordinates_cambia_solo_latitud_y_longitud():
    pinned = ws.pin_coordinates(APP_URL.replace("40.4252", "41.3874").replace("-3.6927", "2.1686"))
    before, after = parse_qs(urlparse(APP_URL).query), parse_qs(urlparse(pinned).query)
    assert (after["latitude"], after["longitude"]) == ([str(ws.MADRID_LAT)], [str(ws.MADRID_LON)])
    assert {k: v for k, v in after.items() if k not in ("latitude", "longitude")} == \
           {k: v for k, v in before.items() if k not in ("latitude", "longitude")}
    assert pinned.startswith(ws.API_URL + "?")


def test_pin_coordinates_agrega_las_coordenadas_si_el_pedido_no_las_trae():
    pinned = ws.pin_coordinates(f"{ws.API_URL}?keywords=piso")
    assert ws.request_coordinates(pinned) == (str(ws.MADRID_LAT), str(ws.MADRID_LON))


def test_request_coordinates():
    assert ws.request_coordinates(APP_URL) == ("40.4252", "-3.6927")
    assert ws.request_coordinates(f"{ws.API_URL}?keywords=x") == (None, None)


def test_el_crawl_registra_la_ruta_que_fija_las_coordenadas(sleeps):
    page = FakePage(FakeApi([[1, 2]]))
    crawl(page)
    ((pattern, handler),) = page.routes
    assert pattern == ws.SECTION_ROUTE and "search/section" in pattern

    sent = []

    async def continue_(url=None):
        sent.append(url)
    asyncio.run(handler(SimpleNamespace(request=SimpleNamespace(url=APP_URL), continue_=continue_)))
    assert ws.request_coordinates(sent[0]) == (str(ws.MADRID_LAT), str(ws.MADRID_LON))


def test_si_reescribir_el_pedido_falla_el_pedido_sigue_sin_cambios(sleeps):
    page = FakePage(FakeApi([[1, 2]]))
    crawl(page)
    handler = page.routes[0][1]
    sent = []

    async def continue_(url=None):
        if url is not None:
            raise RuntimeError("route ya manejada")
        sent.append("sin cambios")
    asyncio.run(handler(SimpleNamespace(request=SimpleNamespace(url=APP_URL), continue_=continue_)))
    assert sent == ["sin cambios"]


def test_la_ruta_se_instala_antes_de_navegar(sleeps):
    page = FakePage(FakeApi([[1, 2]]))
    order = []
    original_route, original_goto = page.route, page.goto

    async def route(*a):
        order.append("route")
        await original_route(*a)

    async def goto(*a, **k):
        order.append("goto")
        return await original_goto(*a, **k)
    page.route, page.goto = route, goto
    crawl(page)
    assert order == ["route", "goto"]


# ── 6. empty_first_page + detalle de la primera respuesta ──────────────────────

@pytest.fixture
def wallapop_log(caplog):
    import logging
    logger = logging.getLogger(ws.__name__)
    logger.disabled = False
    caplog.set_level(logging.INFO, logger=ws.__name__)
    return caplog


def first_page_body(items, meta):
    body = {"data": {"section": {"items": items}}}
    if meta is not ...:
        body["meta"] = meta
    return body


@pytest.mark.parametrize("meta", [{"next_page": "tok2"}, {"next_page": None}, {}])
def test_primera_pagina_sin_anuncios_es_empty_first_page(sleeps, meta):
    page = FakePage(FakeApi([[1]], first_body=first_page_body([], meta)))
    result = crawl(page)
    assert (result.complete, result.incomplete_reason, result.listings) == (False, "empty_first_page", [])
    assert page.fetches == []                                       # no se sigue paginando


def test_primera_pagina_con_items_sin_id_es_empty_first_page(sleeps):
    body = first_page_body([{"title": "sin id"}, {"id": ""}], {"next_page": "tok2"})
    result = crawl(FakePage(FakeApi([[1]], first_body=body)))
    assert (result.complete, result.incomplete_reason) == (False, "empty_first_page")


def test_una_pagina_posterior_vacia_sigue_siendo_repeated_page(sleeps):
    """empty_first_page es solo para la página 1; las demás conservan su motivo."""
    result = crawl(FakePage(FakeApi([[1, 2, 3], [], [4]])))
    assert result.incomplete_reason == "repeated_page"


def test_formato_inesperado_en_la_primera_pagina_sigue_siendo_unparseable(sleeps):
    result = crawl(FakePage(FakeApi([[1]], first_body={"data": {}, "meta": {"next_page": "t"}})))
    assert result.incomplete_reason == "unparseable_page"


def test_empty_first_page_loguea_items_coordenadas_y_claves_de_meta(sleeps, wallapop_log):
    api = FakeApi([[1]], first_body=first_page_body([], {"next_page": "tok2", "x": 1}))
    response = api.first_response()
    response.url = APP_URL
    api.first_response = lambda: response
    crawl(FakePage(api))

    errors = [r.getMessage() for r in wallapop_log.records if r.levelname == "ERROR"]
    (message,) = [m for m in errors if "Primera página sin anuncios" in m]
    for fragment in ("HTTP 200", "ítems=0", "con id=0", "lat=40.4252", "lon=-3.6927",
                     "claves de meta=['next_page', 'x']", "next_page=sí", "claves de body=['data', 'meta']"):
        assert fragment in message


def test_la_primera_respuesta_se_loguea_tambien_cuando_va_bien(sleeps, wallapop_log):
    crawl(FakePage(FakeApi([ids_range(1, 4)])))
    infos = [r.getMessage() for r in wallapop_log.records if "Primera respuesta" in r.getMessage()]
    assert len(infos) == 1 and "ítems=3 (con id=3)" in infos[0] and "next_page=no" in infos[0]
    assert not [r for r in wallapop_log.records if r.levelname == "ERROR"]


def test_empty_first_page_es_un_motivo_aparte():
    from backend.scrapers import base_scraper as bs
    reasons = {bs.EMPTY, bs.EMPTY_FIRST_PAGE, bs.REPEATED_PAGE, bs.BLOCKED, bs.NO_API}
    assert len(reasons) == 5 and bs.EMPTY_FIRST_PAGE == "empty_first_page"
    assert len(bs.EMPTY_FIRST_PAGE) <= 30                           # cabe en scrape_runs.incomplete_reason
