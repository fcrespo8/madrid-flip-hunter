from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class MarketImport(Base):
    """Una carga de precios de mercado desde un archivo. Mismo archivo (mismo sha256) para la misma fuente
    = misma carga: no se repite. Diseño en docs/market_prices_design.md."""
    __tablename__ = "market_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)            # registral_usadas
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    period_label: Mapped[str] = mapped_column(String(30), nullable=False)      # "2007-2025"
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)            # filas insertadas en market_prices
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False)        # filas del archivo sin dato (no se insertan)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "sha256", name="uq_market_imports_source_sha256"),
        CheckConstraint("length(sha256) = 64", name="ck_market_imports_sha256"),
    )


class MarketPrice(Base):
    """Precio medio €/m² de una zona en un período, según una fuente y una carga. Solo se insertan filas:
    una carga nueva con otro precio para el mismo (zona, período) agrega una fila más, no pisa la anterior.
    Las zonas sin dato en un período no tienen fila."""
    __tablename__ = "market_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)                 # fin del período (31/12 del año)
    price_m2: Mapped[float] = mapped_column(Float, nullable=False)
    import_id: Mapped[int] = mapped_column(ForeignKey("market_imports.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("zone_id", "source", "period", "import_id", name="uq_market_prices_zone_source_period_import"),
        CheckConstraint("price_m2 > 0", name="ck_market_prices_price"),
        Index("ix_market_prices_zone_source_period", "zone_id", "source", "period"),
        Index("ix_market_prices_import_id", "import_id"),
    )


class MarketIndex(Base):
    """Un valor de un índice de precios (hoy: el IPV del INE, base 2025 = 100) para una geografía, serie y trimestre.
    Solo se insertan filas: si el INE revisa un valor, se agrega la versión nueva y la más reciente es la que vale."""
    __tablename__ = "market_indices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)            # ine_ipv
    geography: Mapped[str] = mapped_column(String(60), nullable=False)         # Comunidad de Madrid
    series: Mapped[str] = mapped_column(String(20), nullable=False)            # general | nueva | segunda_mano
    period: Mapped[str] = mapped_column(String(6), nullable=False)             # 2026T2
    period_end: Mapped[date] = mapped_column(Date, nullable=False)             # último día del trimestre
    value: Mapped[float] = mapped_column(Float, nullable=False)
    import_id: Mapped[int] = mapped_column(ForeignKey("market_imports.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "geography", "series", "period", "import_id", name="uq_market_indices_point_import"),
        CheckConstraint("series IN ('general', 'nueva', 'segunda_mano')", name="ck_market_indices_series"),
        CheckConstraint("value > 0", name="ck_market_indices_value"),
        Index("ix_market_indices_lookup", "source", "geography", "series", "period_end"),
        Index("ix_market_indices_import_id", "import_id"),
    )
