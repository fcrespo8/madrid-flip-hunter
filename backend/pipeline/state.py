"""State del grafo del pipeline (diseño: docs/refactor-scoring-log.md, parte B).

Solo referencias: IDs, contadores, errores y run_id. Nada de ORM ni Session;
la DB es la fuente de verdad y cada nodo relee lo que necesita.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class NodeError(TypedDict):
    node: str
    error: str
    listing_id: int | None
    source: str | None


class SourceStats(TypedDict):
    new: int
    dup: int
    found: int


def merge_dicts(left: dict, right: dict) -> dict:
    """Reducer para escrituras de nodos en paralelo: cada nodo aporta sus propias claves."""
    return {**(left or {}), **(right or {})}


class PipelineState(TypedDict, total=False):
    run_id: str
    sources: list[str]

    source_stats: dict[str, SourceStats]
    new_ids: list[int]
    deactivated_count: int

    pending_ids: list[int]                                   # select_pending; qa lo reescribe
    qa_rejected_ids: list[int]
    counts: Annotated[dict[str, int], merge_dicts]           # enrich_location (∥ enrich_sizes, TODO)

    unscorable_ids: list[int]
    auto_scored_ids: list[int]
    candidate_ids: list[int]

    scored_ids: Annotated[list[int], operator.add]           # score_one ×N en paralelo
    failed_ids: Annotated[list[int], operator.add]
    notified_ids: list[int]

    errors: Annotated[list[NodeError], operator.add]         # todos los nodos


class ScoreOneInput(TypedDict):
    """Payload de cada Send a score_one."""
    run_id: str
    listing_id: int


def node_error(node: str, error: Exception | str, listing_id: int | None = None,
               source: str | None = None) -> NodeError:
    msg = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    return {"node": node, "error": msg, "listing_id": listing_id, "source": source}
