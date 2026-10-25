from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

ZONE_LEVELS = ("ciudad", "distrito", "barrio")
ALIAS_KINDS = ("ciudad", "distrito", "barrio", "out_of_scope")
ALIAS_SOURCES = ("official", "manual")


class Zone(Base):
    """Zona oficial de Madrid: la ciudad, un distrito o un barrio. Se siembra desde el CSV registral
    (scripts/seed_zones.py); diseño en docs/market_prices_design.md."""
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str | None] = mapped_column(String(10), nullable=True)      # "04", "041"; la ciudad no tiene
    name: Mapped[str] = mapped_column(String(150), nullable=False)           # nombre oficial
    name_norm: Mapped[str] = mapped_column(String(150), nullable=False)      # sin tildes, minúsculas, sin signos
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)

    __table_args__ = (
        CheckConstraint("level IN ('ciudad', 'distrito', 'barrio')", name="ck_zones_level"),
        UniqueConstraint("level", "code", name="uq_zones_level_code"),
        # La ciudad no tiene código (NULL) y UNIQUE (level, code) no ve duplicados con NULL: este índice
        # parcial garantiza que haya una sola fila de nivel 'ciudad'.
        Index("uq_zones_single_ciudad", "level", unique=True,
              postgresql_where=text("level = 'ciudad'"), sqlite_where=text("level = 'ciudad'")),
        Index("ix_zones_name_norm", "name_norm"),
        Index("ix_zones_parent_id", "parent_id"),
    )

    def __repr__(self):
        return f"<Zone {self.level} {self.code} {self.name}>"


class ZoneAlias(Base):
    """Cómo los anuncios nombran una zona. `alias_norm` es único: un texto apunta a una sola zona.
    `out_of_scope` marca textos de otro municipio (sin zona)."""
    __tablename__ = "zone_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias_norm: Mapped[str] = mapped_column(String(150), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(15), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)          # official | manual
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("alias_norm", name="uq_zone_aliases_alias_norm"),
        CheckConstraint("kind IN ('ciudad', 'distrito', 'barrio', 'out_of_scope')", name="ck_zone_aliases_kind"),
        CheckConstraint("source IN ('official', 'manual')", name="ck_zone_aliases_source"),
        CheckConstraint("(kind = 'out_of_scope') = (zone_id IS NULL)", name="ck_zone_aliases_zone_kind"),
        Index("ix_zone_aliases_zone_id", "zone_id"),
    )

    def __repr__(self):
        return f"<ZoneAlias {self.alias_norm!r} → {self.zone_id} ({self.kind})>"
