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


def item(n, title="Piso en venta en Chueca en Madrid"):
    return {"id": f"w{n}", "title": title, "description": "reformar", "web_slug": f"piso-{n}",
            "price": {"amount": 250000.0, "currency": "EUR"}, "location": {"latitude": 40.4, "longitude": -3.7},
            "type_attributes": {"rooms": 2}}


class FakeApi:
    """Catálogo de len(pages) páginas; pages[k-1] = ids de la página k. La página k>=2 se pide con
    ?next_page=tok{k}. `end` es cómo marca la última página: null | empty-string | missing-key | no-meta.
    failures: {página: (status | Exception | "badjson", veces)}."""

    def __init__(self, pages, end="null", failures=None, first_status=200, first_body=None):
        self.pages, self.end, self.failures = pages, end, failures or {}
        self.first_status, self.first_body = first_status, first_body
        self.hits = {}
        self.first_enabled = True

    def body(self, k):
        last = k == len(self.pages)
        body = {"data": {"section": {"items": [item(int(i)) for i in self.pages[k - 1]], "title": "x"}}}
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
        self.handlers, self.gotos, self.fetches = [], 0, []

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
