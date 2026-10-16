from dataclasses import dataclass, field

from backend.models.listing import Listing
from backend.agents.market_prices import get_market_price

AUTO_REASONING = "Score automático: precio vs mercado"
CLAUDE_MIN_PRE_SCORE = 7.0


def unscorable_reason(listing: Listing) -> str | None:
    """Motivo por el que pre_score no puede puntuar el listing, o None si puede."""
    if not listing.price:
        return "no_price"
    if not listing.size_m2:
        return "no_size"
    if not get_market_price(listing.neighborhood, listing.district):
        return "no_market_price"
    return None


def discount_pct(listing: Listing) -> float | None:
    """% por debajo del precio de mercado del €/m² (positivo = más barato que el mercado).
    None si falta precio, tamaño o precio de mercado."""
    market_price = get_market_price(listing.neighborhood, listing.district)
    if not market_price or not listing.size_m2 or not listing.price:
        return None
    return (market_price - listing.price / listing.size_m2) / market_price * 100


def pre_score(listing: Listing) -> float | None:
    discount = discount_pct(listing)
    if discount is None:
        return None

    if discount >= 30:
        return 9.0
    if discount >= 25:
        return 8.0
    if discount >= 20:
        return 7.0
    if discount >= 15:
        return 5.0
    if discount >= 10:
        return 3.0
    return 1.0


def candidate_order(ps: float, discount: float, listing_id: int) -> tuple[float, float, int]:
    """Cola de Claude: pre-score descendente; a igual pre-score, mayor % de descuento sobre el
    mercado primero; a igual descuento, el más viejo (id)."""
    return (-ps, -discount, listing_id)


@dataclass
class PreScoreResult:
    candidates: list[Listing] = field(default_factory=list)   # pre-score >= 7 → Claude, mejores primero
    auto_ids: list[int] = field(default_factory=list)
    unscorable_ids: list[int] = field(default_factory=list)


def apply_pre_scores(db, listings: list[Listing]) -> PreScoreResult:
    """Clasifica los pendientes. Los candidatos quedan en 'pending' para Claude;
    el resto se marca 'auto' (con score) o 'unscorable' (con motivo). Hace commit."""
    result = PreScoreResult()
    ranks: dict[int, tuple[float, float]] = {}   # id → (pre-score, % de descuento)
    for listing in listings:
        reason = unscorable_reason(listing)
        if reason:
            listing.score_status = "unscorable"
            listing.score_status_reason = reason
            result.unscorable_ids.append(listing.id)
            continue

        ps = pre_score(listing)
        if ps >= CLAUDE_MIN_PRE_SCORE:
            result.candidates.append(listing)
            ranks[listing.id] = (ps, discount_pct(listing))
        else:
            listing.score = ps
            listing.score_reasoning = AUTO_REASONING
            listing.score_status = "auto"
            listing.score_status_reason = None
            result.auto_ids.append(listing.id)
    db.commit()
    result.candidates.sort(key=lambda x: candidate_order(*ranks[x.id], x.id))
    return result
