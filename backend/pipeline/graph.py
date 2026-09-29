"""Grafo LangGraph del pipeline completo. Entrypoint nuevo: run_pipeline().

START → scrape → deactivate_stale → select_pending → qa → enrich_location
      → pre_score → score_one (Send ×N) → notify → finalize → END

Sin checkpointer todavía (parte B, paso siguiente). run_all() sigue intacto.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from langgraph.graph import END, START, StateGraph

from backend.pipeline import nodes
from backend.pipeline.state import PipelineState

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 4


def build_pipeline_graph():
    builder = StateGraph(PipelineState)
    builder.add_node("scrape", nodes.scrape)
    builder.add_node("deactivate_stale", nodes.deactivate_stale)
    builder.add_node("select_pending", nodes.select_pending)
    builder.add_node("qa", nodes.qa)
    builder.add_node("enrich_location", nodes.enrich_location)
    # TODO(parte B): add_node("enrich_sizes", ...) en paralelo con enrich_location
    # (qa → [enrich_location ∥ enrich_sizes] → pre_score).
    builder.add_node("pre_score", nodes.pre_score)
    builder.add_node("score_one", nodes.score_one)
    builder.add_node("notify", nodes.notify)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "scrape")
    builder.add_edge("scrape", "deactivate_stale")
    builder.add_edge("deactivate_stale", "select_pending")
    builder.add_edge("select_pending", "qa")
    builder.add_edge("qa", "enrich_location")
    builder.add_edge("enrich_location", "pre_score")
    builder.add_conditional_edges("pre_score", nodes.route_to_scoring, ["score_one", "notify"])
    builder.add_edge("score_one", "notify")
    builder.add_edge("notify", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()


async def run_pipeline(
    sources: list[str] | None = None,
    run_id: str | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    *,
    scrapers: dict | None = None,
    scoring_client=None,
    retriever=None,
) -> PipelineState:
    """Corre el pipeline completo y devuelve el state final (solo IDs y contadores).

    max_concurrency limita las tareas simultáneas del grafo; en la práctica, los
    score_one en paralelo. scrapers/scoring_client/retriever se inyectan en tests.
    """
    run_id = run_id or uuid.uuid4().hex
    configurable = {"thread_id": run_id}
    for key, value in (("scrapers", scrapers), ("scoring_client", scoring_client), ("retriever", retriever)):
        if value is not None:
            configurable[key] = value

    initial: PipelineState = {"run_id": run_id, "sources": sources or nodes.DEFAULT_SOURCES}
    logger.info("Pipeline %s: fuentes=%s max_concurrency=%d", run_id, initial["sources"], max_concurrency)
    return await build_pipeline_graph().ainvoke(
        initial, config={"configurable": configurable, "max_concurrency": max_concurrency}
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline completo como grafo LangGraph (llama a Claude y puede mandar WhatsApp).")
    parser.add_argument("--sources", nargs="+", choices=nodes.DEFAULT_SOURCES, help="default: todas")
    parser.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_pipeline(args.sources, max_concurrency=args.max_concurrency))
