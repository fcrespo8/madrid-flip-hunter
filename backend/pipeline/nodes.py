"""Nodos del grafo del pipeline. Cada uno abre y cierra su propia Session y
reutiliza las funciones existentes; del state solo toma el alcance (IDs).

Dependencias inyectables vía config["configurable"] (tests / prueba manual):
  scrapers        dict nombre → clase de scraper (default: SCRAPERS)
  scoring_client  cliente Anthropic (default: el real)
  retriever       función RAG (default: la real)
  fetch_html      async url → html para enrich_sizes (default: Playwright)
  max_size_fetches  tope de páginas que baja enrich_sizes (default: DEFAULT_MAX_FETCHES)
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import contextmanager

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send

from backend.agents.deactivate_stale import deactivate_stale as _deactivate_stale
from backend.agents.enrich_location import enrich_locations
from backend.agents.enrich_size import DEFAULT_MAX_FETCHES
from backend.agents.enrich_size import enrich_sizes as _enrich_sizes
from backend.agents.pre_scorer import apply_pre_scores
from backend.agents.qa_agent import QAAgent
from backend.models.database import SessionLocal
from backend.models.listing import Listing
from backend.models.repository import pending_listings_query, record_scrape_run, save_listing
from backend.pipeline.state import NodeError, PipelineState, ScoreOneInput, node_error
from backend.scoring.runner import run_scoring
from backend.scrapers.base_scraper import ScrapeResult
from backend.scrapers.donpiso_scraper import DonpisoScraper
from backend.scrapers.redpiso_scraper import RedpisoScraper
from backend.scrapers.remax_scraper import RemaxScraper
from backend.scrapers.run_scrapers import notify_scored
from backend.scrapers.tecnocasa_scraper import TecnocasaScraper
from backend.scrapers.wallapop_scraper import WallapopScraper

logger = logging.getLogger(__name__)

# Las mismas 5 fuentes y en el mismo orden que run_all.
SCRAPERS = {
    "wallapop": WallapopScraper,
    "donpiso": DonpisoScraper,
    "remax": RemaxScraper,
    "redpiso": RedpisoScraper,
    "tecnocasa": TecnocasaScraper,
}
DEFAULT_SOURCES = list(SCRAPERS)


@contextmanager
def _session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _configurable(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable", {})


async def _run_detached(coro):
    """Corre `coro` en un contexto de contextvars vacío. Sin esto, el grafo de
    scoring invocado dentro de un nodo hereda el config del pipeline (incluido el
    checkpointer) y LangGraph intenta checkpointear su ScoringState, que tiene
    objetos ORM y no se puede serializar. Así es una ejecución independiente."""
    return await asyncio.create_task(coro, context=contextvars.Context())


async def scrape(state: PipelineState, config: RunnableConfig) -> dict:
    """Fuentes en serie. Si una falla, se registra y se sigue con la siguiente.
    Cada barrido queda en scrape_runs, con su `complete` y el motivo si no lo fue."""
    scrapers = _configurable(config).get("scrapers", SCRAPERS)
    stats, new_ids, errors = {}, [], []

    for source in state.get("sources") or DEFAULT_SOURCES:
        try:
            result = await scrapers[source]().run()
            new, dup = 0, 0
            with _session() as db:
                for raw in result.listings:
                    listing, created = save_listing(db, raw)
                    if created:
                        new += 1
                        new_ids.append(listing.id)
                    else:
                        dup += 1
                record_scrape_run(db, source, result, new)
            stats[source] = {
                "new": new, "dup": dup, "found": len(result.listings), "complete": result.complete,
                "total_reported": result.total_reported, "incomplete_reason": result.incomplete_reason,
            }
            if result.error:   # el scraper cortó por un error pero devolvió lo que alcanzó a ver
                errors.append(node_error("scrape", result.error, source=source))
            logger.info("[%s] %d nuevos, %d duplicados, barrido %s", source, new, dup,
                        "completo" if result.complete else f"incompleto ({result.incomplete_reason})")
        except Exception as e:
            logger.error("Scraper %s failed: %s", source, e)
            errors.append(node_error("scrape", e, source=source))
            with _session() as db:
                record_scrape_run(db, source, ScrapeResult.failed(f"{type(e).__name__}: {e}"), 0)

    return {"source_stats": stats, "new_ids": new_ids, "errors": errors}


def complete_sources(state: PipelineState) -> list[str]:
    """Fuentes cuyo barrido de esta corrida fue exhaustivo (complete=True).
    Solo de esas se puede inferir que lo que no apareció ya no está publicado."""
    return [src for src, stats in state.get("source_stats", {}).items() if stats.get("complete") is True]


def deactivate_stale(state: PipelineState) -> dict:
    sources = complete_sources(state)
    skipped = {src: stats.get("incomplete_reason") for src, stats in state.get("source_stats", {}).items()
               if src not in sources}
    if skipped:
        logger.info("deactivate_stale: se omiten fuentes con barrido incompleto: %s", skipped)
    return {"deactivated_count": _deactivate_stale(sources)}   # usa su propia sesión


def select_pending(state: PipelineState) -> dict:
    with _session() as db:
        ids = [row.id for row in pending_listings_query(db).with_entities(Listing.id)]
    logger.info("%d listings pendientes", len(ids))
    return {"pending_ids": ids}


def qa(state: PipelineState) -> dict:
    with _session() as db:
        rejected = QAAgent().run(db)["rejected_ids"]
    rejected_set = set(rejected)
    return {
        "qa_rejected_ids": rejected,
        "pending_ids": [i for i in state.get("pending_ids", []) if i not in rejected_set],
    }


def enrich_location(state: PipelineState) -> dict:
    return {"counts": {"located": enrich_locations()}}   # usa su propia sesión


async def enrich_sizes(state: PipelineState, config: RunnableConfig) -> dict:
    """En paralelo con enrich_location. Completa size_m2 (15–1.000 m²) de los
    pendientes de esta corrida y de los 'unscorable' por no_size, a los que vuelve
    a 'pending'. Esos entran en la próxima corrida (pasando por QA), no en esta."""
    conf = _configurable(config)
    counts = await _enrich_sizes(
        pending_ids=state.get("pending_ids", []),
        max_fetches=conf.get("max_size_fetches", DEFAULT_MAX_FETCHES),
        fetch_html=conf.get("fetch_html"),
    )
    return {"counts": counts}


def pre_score(state: PipelineState) -> dict:
    """Relee de la DB los pendientes del alcance (re-ejecutable: ignora lo ya puntuado).
    Con max_llm_calls, solo los primeros N candidatos (por id) van a Claude; el resto
    queda en 'pending' para la próxima corrida."""
    ids = state.get("pending_ids", [])
    with _session() as db:
        listings = pending_listings_query(db).filter(Listing.id.in_(ids)).all() if ids else []
        result = apply_pre_scores(db, listings)
        candidate_ids = [x.id for x in result.candidates]

    cap = state.get("max_llm_calls")
    deferred_ids = candidate_ids[cap:] if cap is not None else []
    candidate_ids = candidate_ids[:cap] if cap is not None else candidate_ids
    logger.info("Pre-score: %d candidatos (%d diferidos por max_llm_calls), %d auto, %d no puntuables",
                len(candidate_ids), len(deferred_ids), len(result.auto_ids), len(result.unscorable_ids))
    return {
        "candidate_ids": candidate_ids,
        "deferred_ids": deferred_ids,
        "auto_scored_ids": result.auto_ids,
        "unscorable_ids": result.unscorable_ids,
    }


def route_to_scoring(state: PipelineState) -> list[Send] | str:
    """Un Send por candidato; sin candidatos va directo a notify."""
    ids = state.get("candidate_ids", [])
    if not ids:
        return "notify"
    return [Send("score_one", {"run_id": state.get("run_id", ""), "listing_id": i}) for i in ids]


async def score_one(payload: ScoreOneInput, config: RunnableConfig) -> dict:
    """Carga el listing por id y lo pasa por run_scoring (grafo de scoring actual,
    con el conteo de intentos y el 'failed' de la parte A)."""
    listing_id = payload["listing_id"]
    conf = _configurable(config)
    with _session() as db:
        listing = db.get(Listing, listing_id)
        if listing is None:
            return {"errors": [node_error("score_one", "listing no encontrado", listing_id)]}
        if listing.score_status != "pending":   # ya procesado (p. ej. re-ejecución)
            logger.info("score_one: listing %s ya está en '%s', se omite", listing_id, listing.score_status)
            return {}

        summary = await _run_detached(run_scoring([listing], db, client=conf.get("scoring_client"),
                                                  retriever=conf.get("retriever")))
        if listing_id in summary.scored_ids:
            return {"scored_ids": [listing_id]}

        errors: list[NodeError] = [node_error("score_one", err, lid) for lid, err in summary.failed]
        out: dict = {"errors": errors}
        if listing.score_status == "failed":
            out["failed_ids"] = [listing_id]
        return out


async def notify(state: PipelineState) -> dict:
    """Reutiliza notify_scored: solo score ≥ 7.5 y sin notified_at."""
    scored_ids = state.get("scored_ids", [])
    if not scored_ids:
        return {"notified_ids": []}
    with _session() as db:
        listings = db.query(Listing).filter(Listing.id.in_(scored_ids)).order_by(Listing.id).all()
        before = {x.id for x in listings if x.notified_at is None}
        await notify_scored(listings, scored_ids, db)
        notified = [x.id for x in listings if x.id in before and x.notified_at is not None]
    return {"notified_ids": notified}


def finalize(state: PipelineState) -> dict:
    logger.info(
        "Pipeline %s terminado: fuentes=%s nuevos=%d desactivados=%d pendientes=%d "
        "rechazados_qa=%d auto=%d no_puntuables=%d candidatos=%d diferidos=%d puntuados=%d failed=%d "
        "notificados=%d counts=%s errores=%d",
        state.get("run_id"), state.get("source_stats", {}), len(state.get("new_ids", [])),
        state.get("deactivated_count", 0), len(state.get("pending_ids", [])),
        len(state.get("qa_rejected_ids", [])), len(state.get("auto_scored_ids", [])),
        len(state.get("unscorable_ids", [])), len(state.get("candidate_ids", [])),
        len(state.get("deferred_ids", [])),
        len(state.get("scored_ids", [])), len(state.get("failed_ids", [])),
        len(state.get("notified_ids", [])), state.get("counts", {}), len(state.get("errors", [])),
    )
    return {}
