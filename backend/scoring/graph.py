"""Grafo LangGraph de scoring: retrieve_rag → score → validate → save.

No notifica: eso lo decide quien llama (ver run_scrapers.run_all).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from backend.scoring.client import (
    ScoreResponse,
    ScoreResult,
    ScoringError,
    request_score,
    validate_score_result,
)
from backend.scoring.prompt import MODEL_ID, SYSTEM_PROMPT, build_user_message
from backend.scoring.rag import get_rag_context

logger = logging.getLogger(__name__)


class ScoringState(TypedDict, total=False):
    listing: Any                    # Listing ORM — no se serializa, sin checkpointing
    db: Any                         # SQLAlchemy Session
    rag_context: str
    response: ScoreResponse | None
    result: ScoreResult | None
    error: str | None
    saved: bool
    _lf_span: Any                   # span raíz de Langfuse, None sin tracing


def _route_on_error(next_node: str) -> Callable[[ScoringState], str]:
    def route(state: ScoringState) -> str:
        return "end" if state.get("error") else next_node
    return route


def build_scoring_graph(client=None, retriever: Callable | None = None):
    """Compila el grafo. `client` y `retriever` se inyectan en tests; None = reales."""

    def retrieve_rag(state: ScoringState) -> dict:
        listing = state["listing"]
        lf_span = state.get("_lf_span")
        lf_rag = None
        if lf_span:
            lf_rag = lf_span.start_observation(
                name="retrieve_rag",
                as_type="retriever",
                input={"neighborhood": listing.neighborhood, "district": listing.district},
            )

        rag_context, docs_count = get_rag_context(state["db"], listing, retriever)

        if lf_rag:
            lf_rag.update(output={"docs_retrieved": docs_count, "has_context": bool(rag_context)})
            lf_rag.end()
        return {"rag_context": rag_context}

    async def score(state: ScoringState) -> dict:
        listing = state["listing"]
        rag_context = state.get("rag_context", "")
        user_message = build_user_message(listing, rag_context)
        lf_span = state.get("_lf_span")

        lf_gen = None
        if lf_span:
            lf_gen = lf_span.start_observation(
                name="llm_score",
                as_type="generation",
                model=MODEL_ID,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                metadata={
                    "listing_id": listing.id,
                    "tool_use": True,
                    "has_rag_context": bool(rag_context),
                },
            )

        try:
            response = await request_score(user_message, client=client)
        except ScoringError as e:
            logger.error("LLM error for listing %s: %s", listing.id, e)
            if lf_gen:
                lf_gen.update(output=None, metadata={"error": str(e)}, level="ERROR")
                lf_gen.end()
            return {"response": None, "error": f"{type(e).__name__}: {e}"}

        if lf_gen:
            lf_gen.update(
                output=response.raw,
                usage_details={"input": response.input_tokens, "output": response.output_tokens},
                metadata={"score": response.raw.get("score") if isinstance(response.raw, dict) else None},
            )
            lf_gen.end()
        return {"response": response}

    def validate(state: ScoringState) -> dict:
        listing = state["listing"]
        try:
            result = validate_score_result(state["response"].raw)
        except ScoringError as e:
            logger.error("Invalid score for listing %s: %s", listing.id, e)
            return {"result": None, "error": f"{type(e).__name__}: {e}"}
        return {"result": result}

    def save(state: ScoringState) -> dict:
        listing = state["listing"]
        db = state["db"]
        result: ScoreResult = state["result"]
        try:
            listing.score = result.score
            listing.score_reasoning = result.reasoning
            listing.score_green_flags = ", ".join(result.green_flags)
            listing.score_red_flags = ", ".join(result.red_flags)
            listing.scored_at = datetime.utcnow()
            db.commit()
        except Exception as e:
            logger.error("DB error saving score for listing %s: %s", listing.id, e)
            db.rollback()
            return {"saved": False, "error": f"DBError: {e}"}

        logger.info("Scored listing %s: %s/10 — %s", listing.id, result.score, result.reasoning)
        return {"saved": True}

    builder = StateGraph(ScoringState)
    builder.add_node("retrieve_rag", retrieve_rag)
    builder.add_node("score", score)
    builder.add_node("validate", validate)
    builder.add_node("save", save)

    builder.set_entry_point("retrieve_rag")
    builder.add_edge("retrieve_rag", "score")
    builder.add_conditional_edges("score", _route_on_error("validate"), {"validate": "validate", "end": END})
    builder.add_conditional_edges("validate", _route_on_error("save"), {"save": "save", "end": END})
    builder.add_edge("save", END)
    return builder.compile()
