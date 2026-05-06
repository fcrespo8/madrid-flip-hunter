import asyncio
import logging
import os
from backend.models.listing import Listing
from backend.agents.market_prices import get_market_price

logger = logging.getLogger(__name__)


async def send_whatsapp_alerts(listings: list[Listing]) -> None:
    sid   = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_ = os.environ.get("TWILIO_WHATSAPP_FROM")
    to    = os.environ.get("TWILIO_WHATSAPP_TO")

    if not all([sid, token, from_, to]):
        logger.warning("Twilio credentials missing — skipping WhatsApp alerts")
        return

    from twilio.rest import Client
    client = Client(sid, token)

    for listing in listings:
        try:
            await asyncio.to_thread(_send_one, client, from_, to, listing)
            logger.info("WhatsApp enviado: %s (score %s)", listing.title, listing.score)
        except Exception as e:
            logger.error("Error enviando WhatsApp para listing %s: %s", listing.id, e)


def _send_one(client, from_: str, to: str, listing: Listing) -> None:
    ppm2 = (
        round(listing.price / listing.size_m2)
        if listing.price and listing.size_m2
        else None
    )
    market_price = get_market_price(listing.neighborhood, listing.district)
    vs_market_pct = (
        round((ppm2 - market_price) / market_price * 100, 1)
        if ppm2 and market_price
        else None
    )

    lines = [
        f"🏠 *DEAL DETECTADO — Score {listing.score}/10*",
        f"📍 {listing.neighborhood or '—'}, {listing.district or '—'}",
        f"💰 {round(listing.price / 1000)}k€ · {ppm2}€/m²" if ppm2 else f"💰 {round(listing.price / 1000)}k€",
    ]

    if vs_market_pct is not None and market_price is not None:
        lines.append(
            f"📊 {vs_market_pct:+.1f}% vs mercado ({round(market_price)}€/m² zona)"
        )

    rooms_str = str(listing.rooms) if listing.rooms is not None else "—"
    size_str  = f"{listing.size_m2}m²" if listing.size_m2 is not None else "—"
    lines.append(f"🛏 {rooms_str} hab · {size_str}")
    lines.append("")
    lines.append(listing.score_reasoning or "")
    lines.append("")
    lines.append(f"🔗 {listing.url}")

    body = "\n".join(lines)
    client.messages.create(from_=from_, to=to, body=body)
