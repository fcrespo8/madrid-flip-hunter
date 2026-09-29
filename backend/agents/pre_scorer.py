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


def pre_score(listing: Listing) -> float | None:
    market_price = get_market_price(listing.neighborhood, listing.district)
    if not market_price or not listing.size_m2 or not listing.price:
        return None

    ppm2 = listing.price / listing.size_m2
    vs_pct = (ppm2 - market_price) / market_price * 100

    if vs_pct <= -30:
        return 9.0
    if vs_pct <= -25:
        return 8.0
    if vs_pct <= -20:
        return 7.0
    if vs_pct <= -15:
        return 5.0
    if vs_pct <= -10:
        return 3.0
    return 1.0


@dataclass
class PreScoreResult:
    candidates: list[Listing] = field(default_factory=list)   # pre-score >= 7 → Claude
    auto_ids: list[int] = field(default_factory=list)
    unscorable_ids: list[int] = field(default_factory=list)


def apply_pre_scores(db, listings: list[Listing]) -> PreScoreResult:
    """Clasifica los pendientes. Los candidatos quedan en 'pending' para Claude;
    el resto se marca 'auto' (con score) o 'unscorable' (con motivo). Hace commit."""
    result = PreScoreResult()
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
        else:
            listing.score = ps
            listing.score_reasoning = AUTO_REASONING
            listing.score_status = "auto"
            listing.score_status_reason = None
            result.auto_ids.append(listing.id)
    db.commit()
    return result
