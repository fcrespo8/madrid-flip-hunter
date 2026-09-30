"""Nodos del grafo del pipeline. Cada uno abre y cierra su propia Session y
reutiliza las funciones existentes; del state solo toma el alcance (IDs).

Dependencias inyectables vía config["configurable"] (tests / prueba manual):
  scrapers        dict nombre → clase de scraper (default: SCRAPERS)
  scoring_client  cliente Anthropic (default: el real)
  retriever       función RAG (default: la real)
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
from backend.agents.pre_scorer import apply_pre_scores
from backend.agents.qa_agent import QAAgent
from backend.models.database import SessionLocal
from backend.models.listing import Listing
from backend.models.repository import pending_listings_query, save_listing
from backend.pipeline.state import NodeError, PipelineState, ScoreOneInput, node_error
from backend.scoring.runner import run_scoring
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
    """Fuentes en serie. Si una falla, se registra y se sigue con la siguiente."""
    scrapers = _configurable(config).get("scrapers", SCRAPERS)
    stats, new_ids, errors = {}, [], []

    for source in state.get("sources") or DEFAULT_SOURCES:
        try:
            raws = await scrapers[source]().run()
            new, dup = 0, 0
            with _session() as db:
                for raw in raws:
                    listing, created = save_listing(db, raw)
                    if created:
                        new += 1
                        new_ids.append(listing.id)
                    else:
                        dup += 1
            stats[source] = {"new": new, "dup": dup, "found": len(raws)}
            logger.info("[%s] %d nuevos, %d duplicados", source, new, dup)
        except Exception as e:
            logger.error("Scraper %s failed: %s", source, e)
            errors.append(node_error("scrape", e, source=source))

    return {"source_stats": stats, "new_ids": new_ids, "errors": errors}


def deactivate_stale(state: PipelineState) -> dict:
    return {"deactivated_count": _deactivate_stale()}   # usa su propia sesión


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


# TODO(parte B): nodo enrich_sizes en paralelo con enrich_location, con la regla
# 15–1.000 m² y el contador unscorable_reset (ver docs/refactor-scoring-log.md).


def pre_score(state: PipelineState) -> dict:
    """Relee de la DB los pendientes del alcance (re-ejecutable: ignora lo ya puntuado)."""
    ids = state.get("pending_ids", [])
    with _session() as db:
        listings = pending_listings_query(db).filter(Listing.id.in_(ids)).all() if ids else []
        result = apply_pre_scores(db, listings)
        candidate_ids = [x.id for x in result.candidates]
    logger.info("Pre-score: %d candidatos, %d auto, %d no puntuables",
                len(candidate_ids), len(result.auto_ids), len(result.unscorable_ids))
    return {
        "candidate_ids": candidate_ids,
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
        "rechazados_qa=%d auto=%d no_puntuables=%d candidatos=%d puntuados=%d failed=%d "
        "notificados=%d errores=%d",
        state.get("run_id"), state.get("source_stats", {}), len(state.get("new_ids", [])),
        state.get("deactivated_count", 0), len(state.get("pending_ids", [])),
        len(state.get("qa_rejected_ids", [])), len(state.get("auto_scored_ids", [])),
        len(state.get("unscorable_ids", [])), len(state.get("candidate_ids", [])),
        len(state.get("scored_ids", [])), len(state.get("failed_ids", [])),
        len(state.get("notified_ids", [])), len(state.get("errors", [])),
    )
    return {}
