from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from backend.agents.market_prices import get_market_price
from backend.agents.notifier import send_whatsapp_alerts
from backend.agents.scoring_agent import SCORE_TOOL, SYSTEM_PROMPT, _anthropic_client
from backend.models.listing import Listing
from backend.rag.retrieval import retrieve_context

logger = logging.getLogger(__name__)


class ScoringState(TypedDict):
    listing_id: int
    listing: Any       # Listing ORM object — not serialized, no checkpointing
    db: Any            # SQLAlchemy Session
    rag_context: str
    listing_ctx: str
    score_result: dict | None
    notified: bool
    error: str | None


# ── Nodes ─────────────────────────────────────────────────────────────────────

def retrieve_rag(state: ScoringState) -> dict:
    listing: Listing = state["listing"]
    db: Session = state["db"]

    rag_context = ""
    if listing.neighborhood and listing.district:
        try:
            docs = retrieve_context(
                db,
                barrio=listing.neighborhood,
                distrito=listing.district,
                top_k=2,
            )
            if docs:
                blocks = "\n\n".join(
                    f"[{d.barrio} - {d.distrito}]\n{d.content}" for d in docs
                )
                rag_context = (
                    "\n\nCONTEXTO CUALITATIVO DEL BARRIO (de tu base de conocimiento):\n"
                    f"{blocks}"
                )
        except Exception as e:
            logger.warning("RAG retrieval failed for listing %s: %s", listing.id, e)

    return {"rag_context": rag_context}


def build_context(state: ScoringState) -> dict:
    listing: Listing = state["listing"]

    price_per_m2 = (
        f"{listing.price / listing.size_m2:.0f}€/m²"
        if listing.size_m2 and listing.size_m2 > 0
        else "desconocido"
    )

    market_price = get_market_price(listing.neighborhood, listing.district)
    market_context = (
        f"{market_price:.0f}€/m² (media del barrio según Idealista abr 2026)"
        if market_price
        else "no disponible"
    )
    vs_market = (
        f"{((listing.price / listing.size_m2) - market_price) / market_price * 100:+.1f}% vs media"
        if market_price and listing.size_m2
        else "no calculable"
    )

    listing_ctx = f"""PISO A EVALUAR:
- Título: {listing.title}
- Precio: {listing.price:,.0f}€
- Tamaño: {listing.size_m2 or 'desconocido'}m²
- Precio/m²: {price_per_m2}
- Precio medio del barrio: {market_context}
- Diferencia vs mercado: {vs_market}
- Habitaciones: {listing.rooms or 'desconocido'}
- Barrio: {listing.neighborhood or 'desconocido'}
- Distrito: {listing.district or 'desconocido'}
- Fuente: {listing.source}
- Descripción: {listing.description or 'Sin descripción'}""".strip()

    return {"listing_ctx": listing_ctx}


async def llm_score(state: ScoringState) -> dict:
    listing: Listing = state["listing"]
    full_context = state["listing_ctx"] + state["rag_context"]

    try:
        response = await _anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[SCORE_TOOL],
            tool_choice={"type": "auto"},
            messages=[
                {
                    "role": "user",
                    "content": f"Evalúa esta oportunidad de flipping:\n\n{full_context}",
                }
            ],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "score_listing":
                return {"score_result": block.input}

        logger.error("Claude did not return tool_use for listing %s", listing.id)
        return {"score_result": None, "error": "no_tool_use"}

    except Exception as e:
        logger.error("LLM error for listing %s: %s", listing.id, e)
        return {"score_result": None, "error": str(e)}


def save_score(state: ScoringState) -> dict:
    listing: Listing = state["listing"]
    db: Session = state["db"]
    result = state.get("score_result")

    if not result:
        return {}

    try:
        listing.score = result["score"]
        listing.score_reasoning = result["reasoning"]
        listing.score_green_flags = ", ".join(result.get("green_flags", []))
        listing.score_red_flags = ", ".join(result.get("red_flags", []))
        db.commit()
        logger.info(
            "Scored listing %s: %s/10 — %s",
            listing.id,
            result["score"],
            result["reasoning"],
        )
    except Exception as e:
        logger.error("DB error saving score for listing %s: %s", listing.id, e)
        db.rollback()

    return {}


async def notify(state: ScoringState) -> dict:
    listing: Listing = state["listing"]
    db: Session = state["db"]

    try:
        await send_whatsapp_alerts([listing])
        listing.notified_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        logger.error("Notification error for listing %s: %s", listing.id, e)
        db.rollback()

    return {"notified": True}


# ── Conditional routing ────────────────────────────────────────────────────────

def route_after_save(state: ScoringState) -> str:
    result = state.get("score_result")
    listing: Listing = state["listing"]
    if result and result["score"] >= 7.5 and listing.notified_at is None:
        return "notify"
    return "end"


# ── Graph assembly ─────────────────────────────────────────────────────────────

_builder = StateGraph(ScoringState)
_builder.add_node("retrieve_rag", retrieve_rag)
_builder.add_node("build_context", build_context)
_builder.add_node("llm_score", llm_score)
_builder.add_node("save_score", save_score)
_builder.add_node("notify", notify)

_builder.set_entry_point("retrieve_rag")
_builder.add_edge("retrieve_rag", "build_context")
_builder.add_edge("build_context", "llm_score")
_builder.add_edge("llm_score", "save_score")
_builder.add_conditional_edges(
    "save_score",
    route_after_save,
    {"notify": "notify", "end": END},
)
_builder.add_edge("notify", END)

scoring_graph = _builder.compile()


# ── Public API ─────────────────────────────────────────────────────────────────

async def run_scoring_graph(listings: list[Listing], db: Session) -> None:
    """Invoke the scoring graph once per listing, sequentially."""
    for listing in listings:
        logger.info("Graph scoring: %s...", listing.title[:60])
        initial_state: ScoringState = {
            "listing_id": listing.id,
            "listing": listing,
            "db": db,
            "rag_context": "",
            "listing_ctx": "",
            "score_result": None,
            "notified": False,
            "error": None,
        }
        try:
            await scoring_graph.ainvoke(initial_state)
        except Exception as e:
            logger.error("Graph failed for listing %s: %s", listing.id, e)
