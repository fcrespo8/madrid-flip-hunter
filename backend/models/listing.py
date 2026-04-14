from sqlalchemy import String, Float, Integer, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from .database import Base


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

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external_id"),
    )

    def price_per_m2(self) -> float | None:
        if self.price and self.size_m2:
            return round(self.price / self.size_m2, 2)
        return None

    def __repr__(self):
        return f"<Listing {self.source} {self.external_id} {self.price}€>"
