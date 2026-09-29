from sqlalchemy import String, Float, Integer, DateTime, Text, UniqueConstraint, Boolean, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from .database import Base

# Ciclo de vida del score:
#   pending    → nuevo o reseteado; lo toma el pipeline
#   auto       → puntuado por pre_score (sin Claude)
#   llm        → puntuado por Claude
#   unscorable → pre_score no puede puntuarlo (score_status_reason: no_price / no_size / no_market_price)
#   failed     → Claude falló MAX_SCORE_ATTEMPTS veces
SCORE_STATUSES = ("pending", "auto", "llm", "unscorable", "failed")


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)

    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    neighborhood: Mapped[str | None] = mapped_column(String(100), nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_green_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_red_flags: Mapped[str | None] = mapped_column(Text, nullable=True)

    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    qa_rejected: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
    qa_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    score_status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external_id"),
        CheckConstraint(
            "score_status IN ('pending', 'auto', 'llm', 'unscorable', 'failed')",
            name="ck_listings_score_status",
        ),
        Index(
            "ix_listings_pending",
            "id",
            postgresql_where=text("score_status = 'pending' AND NOT qa_rejected AND is_active"),
        ),
    )

    def price_per_m2(self) -> float | None:
        if self.price and self.size_m2:
            return round(self.price / self.size_m2, 2)
        return None

    def __repr__(self):
        return f"<Listing {self.source} {self.external_id} {self.price}€>"
