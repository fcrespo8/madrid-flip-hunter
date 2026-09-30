"""Grafo LangGraph del pipeline completo. Entrypoint nuevo: run_pipeline().

START → scrape → deactivate_stale → select_pending → qa → enrich_location
      → pre_score → score_one (Send ×N) → notify → finalize → END

Checkpointer: AsyncPostgresSaver sobre la misma DATABASE_URL (validada por el guard
de backend.models.database), con thread_id = run_id. Si una corrida se corta,
run_pipeline(run_id=X, resume=True) sigue desde el último superstep guardado; los
score_one que ya terminaron no se repiten. run_all() sigue intacto.

Qué NO va al checkpoint: los objetos de config["configurable"] (scrapers, cliente,
retriever). LangGraph solo copia a la metadata los valores str/int/float/bool, así
que no rompen la serialización, pero al retomar hay que volver a pasarlos.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from langgraph.graph import END, START, StateGraph
from sqlalchemy.engine import make_url

from backend.pipeline import nodes
from backend.pipeline.state import PipelineState

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 4


def build_pipeline_graph(checkpointer=None):
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
    return builder.compile(checkpointer=checkpointer)


def checkpointer_conn_string(database_url: str) -> str:
    """URL de SQLAlchemy → conninfo de psycopg 3 (sin '+psycopg2' ni otro driver)."""
    url = make_url(database_url)
    if not url.drivername.startswith("postgres"):
        raise ValueError(f"El checkpointer necesita Postgres; DATABASE_URL usa '{url.drivername}'.")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def _invoke(graph, run_id: str, initial: PipelineState, config: dict, resume: bool) -> PipelineState:
    snapshot = await graph.aget_state(config)
    has_checkpoint = snapshot.created_at is not None
    if resume:
        if not has_checkpoint:
            raise ValueError(f"No hay checkpoint para run_id={run_id!r}: no hay nada que retomar.")
        if not snapshot.next:
            logger.info("Pipeline %s ya había terminado; no se re-ejecuta.", run_id)
            return snapshot.values
        logger.info("Pipeline %s: retomando desde %s", run_id, list(snapshot.next))
        return await graph.ainvoke(None, config=config)
    if has_checkpoint:
        # Con el mismo thread_id, LangGraph sumaría el input nuevo al state viejo
        # (y los reducers acumularían IDs y errores de las dos corridas).
        raise ValueError(f"run_id={run_id!r} ya existe; usá resume=True u otro run_id.")
    return await graph.ainvoke(initial, config=config)


async def run_pipeline(
    sources: list[str] | None = None,
    run_id: str | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    *,
    resume: bool = False,
    checkpointer=None,
    scrapers: dict | None = None,
    scoring_client=None,
    retriever=None,
) -> PipelineState:
    """Corre el pipeline completo y devuelve el state final (solo IDs y contadores).

    - resume=True retoma el run_id dado (input None, mismo thread_id). `sources`
      se ignora: vale la del state guardado.
    - checkpointer: se inyecta en tests (p. ej. InMemorySaver). Sin él se usa
      AsyncPostgresSaver sobre DATABASE_URL y se llama a .setup() al arrancar.
    - max_concurrency limita las tareas simultáneas (en la práctica, los score_one).
    - scrapers/scoring_client/retriever se inyectan en tests; no se guardan en el
      checkpoint, así que al retomar hay que pasarlos de nuevo.
    """
    if resume and not run_id:
        raise ValueError("resume=True necesita el run_id de la corrida a retomar.")
    run_id = run_id or uuid.uuid4().hex
    configurable = {"thread_id": run_id}
    for key, value in (("scrapers", scrapers), ("scoring_client", scoring_client), ("retriever", retriever)):
        if value is not None:
            configurable[key] = value
    config = {"configurable": configurable, "max_concurrency": max_concurrency}

    initial: PipelineState = {"run_id": run_id, "sources": sources or nodes.DEFAULT_SOURCES}
    logger.info("Pipeline %s: fuentes=%s max_concurrency=%d resume=%s",
                run_id, initial["sources"], max_concurrency, resume)

    if checkpointer is not None:
        return await _invoke(build_pipeline_graph(checkpointer), run_id, initial, config, resume)

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from backend.models import database   # el import aplica el guard de producción

    async with AsyncPostgresSaver.from_conn_string(checkpointer_conn_string(database.DATABASE_URL)) as saver:
        await saver.setup()
        return await _invoke(build_pipeline_graph(saver), run_id, initial, config, resume)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline completo como grafo LangGraph (llama a Claude y puede mandar WhatsApp).")
    parser.add_argument("--sources", nargs="+", choices=nodes.DEFAULT_SOURCES, help="default: todas")
    parser.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY)
    parser.add_argument("--run-id", help="id de la corrida (default: uno nuevo)")
    parser.add_argument("--resume", action="store_true", help="retomar --run-id desde su último checkpoint")
    args = parser.parse_args()
    if args.resume and not args.run_id:
        parser.error("--resume necesita --run-id")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_pipeline(args.sources, run_id=args.run_id, max_concurrency=args.max_concurrency,
                             resume=args.resume))
