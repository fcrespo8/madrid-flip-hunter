"""PlaywrightFetcher de enrich_sizes: user_agent normal y HTTP >= 400 como descarga fallida.
Offline: un Playwright falso; nada sale a la red."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.agents import enrich_size as es
from tests.test_scoring import FakeDB, _offline, make_listing  # noqa: F401

CLOUDFRONT_403 = ("<html><head><title>ERROR: The request could not be satisfied</title></head>"
                  "<body><h1>403 ERROR</h1>Request blocked.</body></html>")
GOOD_PAGE = "<html><body><h1>Piso en Chueca</h1><p>Superficie: 62 m²</p></body></html>"
PAGE_WITHOUT_SIZE = "<html><body><h1>Piso en Chueca</h1><p>Sin datos de superficie</p></body></html>"


class FakePage:
    def __init__(self, status=200, html=GOOD_PAGE, no_response=False, goto_error=None):
        self.status, self.html, self.no_response, self.goto_error = status, html, no_response, goto_error
        self.goto_calls = []

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        if self.goto_error:
            raise self.goto_error
        return None if self.no_response else SimpleNamespace(status=self.status)

    async def content(self):
        return self.html


class FakeContext:
    def __init__(self, page):
        self.page, self.closed = page, False

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.page, self.context_kwargs, self.contexts, self.closed = page, [], [], False

    async def new_context(self, **kwargs):
        self.context_kwargs.append(kwargs)
        context = FakeContext(self.page)
        self.contexts.append(context)
        return context

    async def close(self):
        self.closed = True


@pytest.fixture
def fetcher_env(monkeypatch):
    """fetcher_env(page) instala un Playwright falso y devuelve el navegador falso."""
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    class FakeStealth:
        async def apply_stealth_async(self, page):
            pass
    monkeypatch.setattr(es, "Stealth", FakeStealth)

    def install(page):
        browser = FakeBrowser(page)

        class Playwright:
            chromium = SimpleNamespace(launch=_async_value(browser))

            async def stop(self):
                pass
        monkeypatch.setattr(es, "async_playwright", lambda: SimpleNamespace(start=_async_value(Playwright())))
        browser.sleeps = sleeps
        return browser
    return install


def _async_value(value):
    async def f(*args, **kwargs):
        return value
    return f


def fetch(url, page):
    """Corre PlaywrightFetcher contra la página falsa. Devuelve (html | excepción, navegador)."""
    async def run():
        async with es.PlaywrightFetcher() as fetcher:
            try:
                return await fetcher(url)
            except Exception as e:
                return e
    return run


# ── 1. user_agent normal ───────────────────────────────────────────────────────

def test_el_contexto_usa_un_user_agent_normal(fetcher_env):
    browser = fetcher_env(FakePage())
    asyncio.run(fetch("https://es.wallapop.com/item/x", None)())
    (kwargs,) = browser.context_kwargs
    assert kwargs["locale"] == "es-ES" and kwargs["user_agent"] == es.USER_AGENT
    assert "headless" not in es.USER_AGENT.lower()           # "HeadlessChrome" en el header → 403 de CloudFront
    assert es.USER_AGENT.startswith("Mozilla/5.0") and "Chrome/" in es.USER_AGENT


# ── 2. HTTP >= 400 es una descarga fallida ─────────────────────────────────────

def test_una_descarga_ok_devuelve_el_html_y_espera_2s(fetcher_env):
    browser = fetcher_env(FakePage(html=GOOD_PAGE))
    html = asyncio.run(fetch("https://example.com/p", None)())
    assert html == GOOD_PAGE
    assert browser.sleeps == [2]                              # la espera de siempre, tras comprobar el status
    assert browser.contexts[0].closed is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 500, 502, 503])
def test_http_400_o_mas_levanta_download_error(fetcher_env, status):
    page = FakePage(status=status, html=CLOUDFRONT_403)
    browser = fetcher_env(page)
    result = asyncio.run(fetch("https://es.wallapop.com/item/x", None)())
    assert isinstance(result, es.DownloadError)
    assert result.status == status and f"HTTP {status}" in str(result) and "wallapop.com/item/x" in str(result)
    assert browser.contexts[0].closed is True                 # el contexto se cierra aunque falle
    assert browser.sleeps == []                               # y no se pierde tiempo esperando una página de error


def test_un_399_o_menos_no_es_error(fetcher_env):
    fetcher_env(FakePage(status=304, html=GOOD_PAGE))
    assert asyncio.run(fetch("https://example.com/p", None)()) == GOOD_PAGE


def test_sin_respuesta_levanta_download_error(fetcher_env):
    browser = fetcher_env(FakePage(no_response=True))
    result = asyncio.run(fetch("https://example.com/p", None)())
    assert isinstance(result, es.DownloadError) and result.status is None and "sin respuesta" in str(result)
    assert browser.contexts[0].closed is True


def test_un_timeout_de_navegacion_se_propaga_y_cierra_el_contexto(fetcher_env):
    browser = fetcher_env(FakePage(goto_error=TimeoutError("networkidle excedido")))
    result = asyncio.run(fetch("https://example.com/p", None)())
    assert isinstance(result, TimeoutError)
    assert browser.contexts[0].closed is True


# ── 3. Integración: una descarga fallida NO termina en no_size_final ───────────

def make_unsized(**overrides):
    return make_listing(id=1, url="https://es.wallapop.com/item/x", size_m2=None, score_status="unscorable",
                        score_status_reason="no_size", **overrides)


def enrich_with(page, fetcher_env):
    browser = fetcher_env(page)
    listing, db = make_unsized(), FakeDB()

    async def run():
        async with es.PlaywrightFetcher() as fetcher:
            return await es.enrich_listing_sizes(db, [listing], fetcher)
    return asyncio.run(run()), listing, db, browser


def test_el_403_de_cloudfront_ya_no_deja_el_listing_en_no_size_final(fetcher_env):
    """El bug: la página de bloqueo se tomaba por 'se bajó bien y no trae m²'."""
    counts, listing, db, _ = enrich_with(FakePage(status=403, html=CLOUDFRONT_403), fetcher_env)
    assert counts == {"sized": 0, "size_rejected": 0, "size_not_found": 0, "fetch_failed": 1, "unscorable_reset": 0}
    assert (listing.score_status, listing.score_status_reason, listing.size_m2) == ("unscorable", "no_size", None)
    assert db.commits == 0                                    # no se tocó nada: se reintenta en otra corrida


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_cualquier_http_de_error_queda_para_reintentar(fetcher_env, status):
    counts, listing, _, _ = enrich_with(FakePage(status=status, html="<html>error</html>"), fetcher_env)
    assert counts["fetch_failed"] == 1 and counts["size_not_found"] == 0
    assert listing.score_status_reason == "no_size"           # sigue seleccionable por size_enrichment_query


def test_una_pagina_200_sin_m2_sigue_siendo_no_size_final(fetcher_env):
    """Lo que sí es definitivo: la página se bajó bien (HTTP 200) y no trae tamaño."""
    counts, listing, db, _ = enrich_with(FakePage(status=200, html=PAGE_WITHOUT_SIZE), fetcher_env)
    assert counts["size_not_found"] == 1 and counts["fetch_failed"] == 0
    assert (listing.score_status, listing.score_status_reason) == ("unscorable", "no_size_final")
    assert db.commits == 1


def test_una_pagina_200_con_m2_completa_el_tamano(fetcher_env):
    counts, listing, _, _ = enrich_with(FakePage(status=200, html=GOOD_PAGE), fetcher_env)
    assert counts["sized"] == 1 and counts["unscorable_reset"] == 1
    assert (listing.size_m2, listing.score_status, listing.score_status_reason) == (62.0, "pending", None)


class RoutingPage:
    """Página falsa que responde según la URL pedida (status, html)."""

    def __init__(self, routes):
        self.routes, self.current = routes, None

    async def goto(self, url, wait_until=None, timeout=None):
        status, self.current = self.routes[url]
        return SimpleNamespace(status=status)

    async def content(self):
        return self.current


def test_un_lote_mezcla_exitos_y_descargas_fallidas(fetcher_env):
    """Con el PlaywrightFetcher real: una página bloqueada no frena al resto ni se confunde con 'sin tamaño'."""
    browser = fetcher_env(RoutingPage({"https://a/1": (200, GOOD_PAGE), "https://a/2": (403, CLOUDFRONT_403),
                                       "https://a/3": (200, PAGE_WITHOUT_SIZE)}))
    listings = [make_listing(id=i, url=f"https://a/{i}", size_m2=None, score_status="unscorable",
                             score_status_reason="no_size") for i in (1, 2, 3)]

    async def run():
        async with es.PlaywrightFetcher() as fetcher:
            return await es.enrich_listing_sizes(FakeDB(), listings, fetcher)
    counts = asyncio.run(run())

    assert counts == {"sized": 1, "size_rejected": 0, "size_not_found": 1, "fetch_failed": 1, "unscorable_reset": 1}
    assert [x.score_status_reason for x in listings] == [None, "no_size", "no_size_final"]
    assert len(browser.contexts) == 3 and all(c.closed for c in browser.contexts)
    assert all(k["user_agent"] == es.USER_AGENT for k in browser.context_kwargs)       # cada página con el user_agent normal
