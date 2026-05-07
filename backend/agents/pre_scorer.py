from backend.models.listing import Listing
from backend.agents.market_prices import get_market_price


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
