from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ScrapeRun(Base):
    """Un barrido de una fuente. Sirve para ver de un vistazo qué fuentes ven el
    catálogo completo y cuáles no (deactivate_stale solo actúa sobre las completas)."""
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    seen_count: Mapped[int] = mapped_column(Integer, nullable=False)   # listings únicos que trajo el scraper
    new_count: Mapped[int] = mapped_column(Integer, nullable=False)    # los que no estaban en la DB
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    total_reported: Mapped[int | None] = mapped_column(Integer, nullable=True)   # total que informa el sitio
    incomplete_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_scrape_runs_source_created_at", "source", "created_at"),
    )
