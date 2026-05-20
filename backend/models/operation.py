import enum
import uuid
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    String, Float, Integer, Text, Boolean, Numeric, Date, DateTime,
    ForeignKey, Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from .database import Base


class OperationStatus(enum.Enum):
    prospecto   = "prospecto"
    negociacion = "negociacion"
    compra      = "compra"
    en_obra     = "en_obra"
    en_venta    = "en_venta"
    vendido     = "vendido"


class ExpenseCategory(enum.Enum):
    precio_piso   = "precio_piso"   # precio de escritura del inmueble
    compra        = "compra"        # notaría, registro, tasas (excluye precio del piso)
    reforma       = "reforma"
    reforma_extra = "reforma_extra"
    suministros   = "suministros"
    comunidad     = "comunidad"
    honorarios    = "honorarios"
    agencia       = "agencia"
    financiacion  = "financiacion"
    impuestos     = "impuestos"
    otros         = "otros"


class PaidBy(enum.Enum):
    francisco = "francisco"
    german    = "german"
    sl        = "sl"
    ambos     = "ambos"


class Frequency(enum.Enum):
    monthly   = "monthly"
    quarterly = "quarterly"
    annual    = "annual"


class UserRole(enum.Enum):
    admin  = "admin"
    viewer = "viewer"


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[OperationStatus] = mapped_column(SAEnum(OperationStatus), nullable=False, default=OperationStatus.prospecto)

    address:      Mapped[str | None] = mapped_column(String(300), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(100), nullable=True)
    district:     Mapped[str | None] = mapped_column(String(100), nullable=True)
    lat:          Mapped[float | None] = mapped_column(Float, nullable=True)
    lon:          Mapped[float | None] = mapped_column(Float, nullable=True)

    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    notes:      Mapped[str | None] = mapped_column(Text, nullable=True)
    metros:     Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    financials: Mapped["OperationFinancials | None"] = relationship("OperationFinancials", back_populates="operation", uselist=False)
    dates:      Mapped["OperationDates | None"]      = relationship("OperationDates",      back_populates="operation", uselist=False)
    expenses:   Mapped[list["OperationExpense"]]     = relationship("OperationExpense",    back_populates="operation")
    distributions: Mapped[list["PartnerDistribution"]] = relationship("PartnerDistribution", back_populates="operation")
    op_partners: Mapped[list["OperationPartner"]]    = relationship("OperationPartner",    back_populates="operation")


class OperationFinancials(Base):
    __tablename__ = "operation_financials"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"), unique=True, nullable=False)

    purchase_price:   Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    purchase_taxes:   Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    purchase_notary:  Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    renovation_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    target_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    sale_agency_fee:   Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    sale_tax_estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    financing_own_capital:    Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    financing_borrowed:       Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    financing_cost:           Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    financing_interest_rate:  Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    financing_loan_months:    Mapped[int | None]     = mapped_column(Integer, nullable=True)

    buy_commission: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    tax_regime:     Mapped[str | None]     = mapped_column(String(20), nullable=True)

    operation: Mapped["Operation"] = relationship("Operation", back_populates="financials")


class OperationDates(Base):
    __tablename__ = "operation_dates"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"), unique=True, nullable=False)

    offer_date:        Mapped[date | None] = mapped_column(Date, nullable=True)
    arras_date:        Mapped[date | None] = mapped_column(Date, nullable=True)
    escritura_date:    Mapped[date | None] = mapped_column(Date, nullable=True)
    renovation_start:  Mapped[date | None] = mapped_column(Date, nullable=True)
    renovation_end:    Mapped[date | None] = mapped_column(Date, nullable=True)
    listing_date:      Mapped[date | None] = mapped_column(Date, nullable=True)
    sale_date:         Mapped[date | None] = mapped_column(Date, nullable=True)

    operation: Mapped["Operation"] = relationship("Operation", back_populates="dates")


class OperationExpense(Base):
    __tablename__ = "operation_expenses"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)

    date:           Mapped[date]     = mapped_column(Date, nullable=False)
    description:    Mapped[str]      = mapped_column(String(300), nullable=False)
    category:       Mapped[ExpenseCategory] = mapped_column(SAEnum(ExpenseCategory), nullable=False)
    amount:         Mapped[Decimal]  = mapped_column(Numeric(12, 2), nullable=False)
    paid_by:        Mapped[PaidBy]   = mapped_column(SAEnum(PaidBy), nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes:          Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    operation: Mapped["Operation | None"] = relationship("Operation", back_populates="expenses")


class OperationPartner(Base):
    __tablename__ = "operation_partners"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)

    name:              Mapped[str]            = mapped_column(String(200), nullable=False)
    role:              Mapped[str | None]     = mapped_column(String(100), nullable=True)
    participation_pct: Mapped[Decimal]        = mapped_column(Numeric(5, 2), nullable=False)
    capital_contributed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    loan_amount:       Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    loan_interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    loan_months:       Mapped[int | None]     = mapped_column(Integer, nullable=True)
    created_at:        Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    operation: Mapped["Operation"] = relationship("Operation", back_populates="op_partners")


class RecurringExpense(Base):
    __tablename__ = "recurring_expenses"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)

    description:   Mapped[str]      = mapped_column(String(300), nullable=False)
    amount:        Mapped[Decimal]  = mapped_column(Numeric(12, 2), nullable=False)
    frequency:     Mapped[Frequency] = mapped_column(SAEnum(Frequency), nullable=False)
    category:      Mapped[ExpenseCategory] = mapped_column(SAEnum(ExpenseCategory), nullable=False)
    start_date:    Mapped[date]     = mapped_column(Date, nullable=False)
    end_date:      Mapped[date | None] = mapped_column(Date, nullable=True)
    auto_generate: Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Partner(Base):
    __tablename__ = "partners"

    id:                Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name:              Mapped[str]      = mapped_column(String(200), nullable=False)
    participation_pct: Mapped[Decimal]  = mapped_column(Numeric(5, 2), nullable=False)
    joined_date:       Mapped[date | None] = mapped_column(Date, nullable=True)

    distributions: Mapped[list["PartnerDistribution"]] = relationship("PartnerDistribution", back_populates="partner")


class PartnerDistribution(Base):
    __tablename__ = "partner_distributions"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    partner_id:   Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("partners.id"), nullable=False)

    amount:         Mapped[Decimal]  = mapped_column(Numeric(12, 2), nullable=False)
    distributed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    operation: Mapped["Operation"] = relationship("Operation", back_populates="distributions")
    partner:   Mapped["Partner"]   = relationship("Partner",   back_populates="distributions")


class User(Base):
    __tablename__ = "users"

    id:              Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username:        Mapped[str]      = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str]      = mapped_column(String(300), nullable=False)
    role:            Mapped[UserRole] = mapped_column(SAEnum(UserRole), nullable=False, default=UserRole.viewer)
    created_at:      Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
