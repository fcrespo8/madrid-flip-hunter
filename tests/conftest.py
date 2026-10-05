"""Aísla los tests de la DB real.

pytest carga este archivo antes que cualquier módulo de test, así que lo que se
fija acá gana sobre `load_dotenv()` (que no pisa variables ya definidas).

- DATABASE_URL siempre se reemplaza: nunca se usa la del .env ni la del shell.
- Con TEST_DATABASE_URL, los tests que tocan DB corren contra esa base, que
  debe tener "test" en el nombre y ser distinta de la del .env. Si no, pytest
  aborta antes de correr nada.
- Sin TEST_DATABASE_URL se usa una URL ficticia y esos tests se saltean.
"""
import os

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

DUMMY_DATABASE_URL = "postgresql://test@localhost/test"

_LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}
_DEFAULT_PORTS = {"postgresql": 5432}


def _db_identity(url: str) -> tuple[str, int | None, str]:
    """(host, puerto, base) normalizados: localhost == 127.0.0.1 == socket local,
    puerto por defecto explícito. Ignora usuario, password y driver."""
    u = make_url(url)
    host = "localhost" if u.host in _LOCAL_HOSTS else u.host.lower()
    port = u.port or _DEFAULT_PORTS.get(u.get_backend_name())
    return host, port, (u.database or "").lower()


def _resolve_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.exit("En CI hace falta TEST_DATABASE_URL: los tests de DB no pueden saltearse.", returncode=2)
        return DUMMY_DATABASE_URL

    db_name = make_url(url).database or ""
    if "test" not in db_name.lower():
        pytest.exit(f"TEST_DATABASE_URL apunta a '{db_name}': el nombre de la DB debe contener 'test'.", returncode=2)

    dotenv_url = dotenv_values().get("DATABASE_URL")
    if dotenv_url and _db_identity(dotenv_url) == _db_identity(url):
        pytest.exit("TEST_DATABASE_URL es la misma DB que DATABASE_URL del .env.", returncode=2)
    return url


TEST_DATABASE_URL = _resolve_test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

requires_test_db = pytest.mark.skipif(
    TEST_DATABASE_URL == DUMMY_DATABASE_URL,
    reason="Definí TEST_DATABASE_URL (una DB con 'test' en el nombre) para correr este test",
)


def pytest_sessionstart(session):
    """Red de seguridad: el engine tiene que haber quedado apuntando a la DB de test."""
    from backend.models.database import engine

    if engine.url != make_url(TEST_DATABASE_URL):
        pytest.exit(f"El engine apunta a {engine.url.render_as_string(hide_password=True)}, no a la DB de test.",
                    returncode=2)


# ── Fakes del grafo del pipeline (los usan test_pipeline_checkpoint y test_pipeline_c1) ──

from collections import Counter  # noqa: E402


class Boom(RuntimeError):
    pass


@pytest.fixture
def fake_nodes(monkeypatch):
    """Reemplaza cada nodo por un fake que registra sus llamadas. `fail_once` hace
    que un nodo (o un score_one de un listing) falle la primera vez."""
    from backend.pipeline import nodes
    calls = Counter()
    fail_once = set()

    def maybe_fail(key):
        calls[key] += 1
        if key in fail_once:
            fail_once.discard(key)
            raise Boom(f"falla simulada en {key}")

    def sync(name, out):
        def node(state):
            maybe_fail(name)
            return out(state) if callable(out) else out
        return node

    async def scrape(state, config):
        maybe_fail("scrape")
        return {"source_stats": {"fake": {"new": 3, "dup": 0, "found": 3}}, "new_ids": [1, 2, 3]}

    async def score_one(payload, config):
        maybe_fail(f"score_one:{payload['listing_id']}")
        return {"scored_ids": [payload["listing_id"]]}

    async def notify(state):
        maybe_fail("notify")
        return {"notified_ids": sorted(state.get("scored_ids", []))}

    monkeypatch.setattr(nodes, "scrape", scrape)
    monkeypatch.setattr(nodes, "deactivate_stale", sync("deactivate_stale", {"deactivated_count": 0}))
    monkeypatch.setattr(nodes, "select_pending", sync("select_pending", {"pending_ids": [1, 2, 3]}))
    monkeypatch.setattr(nodes, "qa", sync("qa", lambda s: {"qa_rejected_ids": [], "pending_ids": s["pending_ids"]}))
    monkeypatch.setattr(nodes, "enrich_location", sync("enrich_location", {"counts": {"located": 3}}))

    async def enrich_sizes(state, config):
        maybe_fail("enrich_sizes")
        return {"counts": {"sized": 1}}

    monkeypatch.setattr(nodes, "enrich_sizes", enrich_sizes)
    monkeypatch.setattr(nodes, "pre_score", sync("pre_score", {
        "candidate_ids": [1, 2, 3], "auto_scored_ids": [], "unscorable_ids": []}))
    monkeypatch.setattr(nodes, "score_one", score_one)
    monkeypatch.setattr(nodes, "notify", notify)
    monkeypatch.setattr(nodes, "finalize", sync("finalize", {}))
    return calls, fail_once
