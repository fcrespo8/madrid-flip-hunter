from __future__ import annotations
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import Operation, OperationStatus, OperationFinancials, OperationExpense, ExpenseCategory
from backend.auth.dependencies import get_current_user, require_admin
from backend.models.operation import User

router = APIRouter(prefix="/api/operations", tags=["operations"])

# ── Schemas ──────────────────────────────────────────────────────────────────

class OperationCreate(BaseModel):
    name: str
    status: str = "prospecto"
    address: Optional[str] = None
    neighborhood: Optional[str] = None
    district: Optional[str] = None
    notes: Optional[str] = None
    listing_id: Optional[int] = None


class OperationUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    address: Optional[str] = None
    neighborhood: Optional[str] = None
    district: Optional[str] = None
    notes: Optional[str] = None
    listing_id: Optional[int] = None


class OperationOut(BaseModel):
    id: str
    name: str
    status: str
    address: Optional[str]
    neighborhood: Optional[str]
    district: Optional[str]
    notes: Optional[str]
    listing_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    roi_pct:    Optional[float] = None
    net_profit: Optional[float] = None

    @classmethod
    def from_orm(cls, op: Operation) -> "OperationOut":
        return cls(
            id=str(op.id),
            name=op.name,
            status=op.status.value,
            address=op.address,
            neighborhood=op.neighborhood,
            district=op.district,
            notes=op.notes,
            listing_id=op.listing_id,
            created_at=op.created_at,
            updated_at=op.updated_at,
        )


class FinancialsUpdate(BaseModel):
    purchase_price:    Optional[Decimal] = None
    purchase_taxes:    Optional[Decimal] = None
    purchase_notary:   Optional[Decimal] = None
    buy_commission:    Optional[Decimal] = None
    renovation_budget: Optional[Decimal] = None
    target_sale_price: Optional[Decimal] = None
    actual_sale_price: Optional[Decimal] = None
    sale_agency_fee:   Optional[Decimal] = None
    sale_tax_estimate: Optional[Decimal] = None
    financing_own_capital:   Optional[Decimal] = None
    financing_borrowed:      Optional[Decimal] = None
    financing_cost:          Optional[Decimal] = None
    financing_interest_rate: Optional[Decimal] = None
    financing_loan_months:   Optional[int]     = None
    tax_regime:              Optional[str]     = None


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _build_financials_out(
    fin: OperationFinancials | None,
    total_expenses: float,
    expenses_by_category: dict | None = None,
) -> dict:
    ebc = expenses_by_category or {"obra": 0.0, "tramites": 0.0, "otros": 0.0,
                                   "agencia_venta": 0.0, "financiacion": 0.0, "impuestos_gastos": 0.0}
    empty = {
        "purchase_price": None, "purchase_taxes": None, "purchase_notary": None,
        "buy_commission": None, "renovation_budget": None,
        "target_sale_price": None, "actual_sale_price": None,
        "sale_agency_fee": None, "sale_tax_estimate": None,
        "financing_own_capital": None, "financing_borrowed": None, "financing_cost": None,
        "financing_interest_rate": None, "financing_loan_months": None,
        "tax_regime": None,
        "total_purchase_cost": None, "total_expenses": total_expenses,
        "expenses_by_category": ebc,
        "gross_profit": None, "net_profit": None, "roi_pct": None,
    }
    if fin is None:
        return empty

    pp  = _f(fin.purchase_price)  or 0
    pt  = _f(fin.purchase_taxes)  or 0
    pn  = _f(fin.purchase_notary) or 0
    bc  = _f(fin.buy_commission)  or 0
    asp = _f(fin.actual_sale_price)
    saf = _f(fin.sale_agency_fee)  or 0
    ste = _f(fin.sale_tax_estimate) or 0
    fc  = _f(fin.financing_cost)   or 0
    foc = _f(fin.financing_own_capital) or 0
    fib = _f(fin.financing_borrowed)    or 0

    total_pc = pp + pt + pn + bc

    gross_profit: float | None = None
    net_profit:   float | None = None
    roi_pct:      float | None = None
    if asp is not None:
        gross_profit = asp - total_pc - total_expenses - saf - ste - fc
        net_profit   = gross_profit
        denom = foc + fib
        if denom > 0:
            roi_pct = round(net_profit / denom * 100, 2)

    return {
        "purchase_price":    _f(fin.purchase_price),
        "purchase_taxes":    _f(fin.purchase_taxes),
        "purchase_notary":   _f(fin.purchase_notary),
        "buy_commission":    _f(fin.buy_commission),
        "renovation_budget": _f(fin.renovation_budget),
        "target_sale_price": _f(fin.target_sale_price),
        "actual_sale_price": _f(fin.actual_sale_price),
        "sale_agency_fee":   _f(fin.sale_agency_fee),
        "sale_tax_estimate": _f(fin.sale_tax_estimate),
        "financing_own_capital":   _f(fin.financing_own_capital),
        "financing_borrowed":      _f(fin.financing_borrowed),
        "financing_cost":          _f(fin.financing_cost),
        "financing_interest_rate": _f(fin.financing_interest_rate),
        "financing_loan_months":   fin.financing_loan_months,
        "tax_regime": fin.tax_regime,
        "total_purchase_cost": round(total_pc, 2),
        "total_expenses":      round(total_expenses, 2),
        "expenses_by_category": {k: round(v, 2) for k, v in ebc.items()},
        "gross_profit": round(gross_profit, 2) if gross_profit is not None else None,
        "net_profit":   round(net_profit,   2) if net_profit   is not None else None,
        "roi_pct":      roi_pct,
    }


def _get_expenses_data(db: Session, op_id: uuid.UUID) -> tuple[float, dict]:
    expenses = db.query(OperationExpense).filter_by(operation_id=op_id).all()
    total = float(sum(e.amount for e in expenses))
    by_cat: dict[str, float] = {
        "obra": 0.0, "tramites": 0.0, "otros": 0.0,
        "agencia_venta": 0.0, "financiacion": 0.0, "impuestos_gastos": 0.0,
    }
    obra_cats     = {ExpenseCategory.reforma, ExpenseCategory.reforma_extra}
    otros_cats    = {ExpenseCategory.comunidad, ExpenseCategory.suministros, ExpenseCategory.otros}
    for e in expenses:
        cat = e.category
        if cat in obra_cats:
            by_cat["obra"] += float(e.amount)
        elif cat == ExpenseCategory.honorarios:
            by_cat["tramites"] += float(e.amount)
        elif cat in otros_cats:
            by_cat["otros"] += float(e.amount)
        elif cat == ExpenseCategory.agencia:
            by_cat["agencia_venta"] += float(e.amount)
        elif cat == ExpenseCategory.financiacion:
            by_cat["financiacion"] += float(e.amount)
        elif cat == ExpenseCategory.impuestos:
            by_cat["impuestos_gastos"] += float(e.amount)
    return total, by_cat


def _parse_status(value: str) -> OperationStatus:
    try:
        return OperationStatus(value)
    except ValueError:
        valid = [s.value for s in OperationStatus]
        raise HTTPException(status_code=400, detail=f"Invalid status '{value}'. Valid: {valid}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", response_model=list[OperationOut])
def list_operations(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ops = db.query(Operation).order_by(Operation.created_at.desc()).all()
    result = []
    for op in ops:
        out = OperationOut.from_orm(op)
        total_exp, by_cat = _get_expenses_data(db, op.id)
        fin_data = _build_financials_out(op.financials, total_exp, by_cat)
        out.roi_pct    = fin_data.get("roi_pct")
        out.net_profit = fin_data.get("net_profit")
        result.append(out)
    return result


@router.get("/{op_id}", response_model=OperationOut)
def get_operation(
    op_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    op = db.query(Operation).filter_by(id=uuid.UUID(op_id)).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    return OperationOut.from_orm(op)


@router.post("/", response_model=OperationOut, status_code=201)
def create_operation(
    body: OperationCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    status = _parse_status(body.status)
    op = Operation(
        name=body.name,
        status=status,
        address=body.address,
        neighborhood=body.neighborhood,
        district=body.district,
        notes=body.notes,
        listing_id=body.listing_id,
    )
    db.add(op)
    db.commit()
    db.refresh(op)
    return OperationOut.from_orm(op)


@router.patch("/{op_id}", response_model=OperationOut)
def update_operation(
    op_id: str,
    body: OperationUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    op = db.query(Operation).filter_by(id=uuid.UUID(op_id)).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "status" and value is not None:
            value = _parse_status(value)
        setattr(op, field, value)
    op.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(op)
    return OperationOut.from_orm(op)


@router.patch("/{op_id}/status", response_model=OperationOut)
def update_status(
    op_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    op = db.query(Operation).filter_by(id=uuid.UUID(op_id)).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    status_value = body.get("status")
    if not status_value:
        raise HTTPException(status_code=400, detail="Field 'status' is required")
    op.status = _parse_status(status_value)
    op.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(op)
    return OperationOut.from_orm(op)


@router.get("/{op_id}/financials")
def get_financials(
    op_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    uid = uuid.UUID(op_id)
    op = db.query(Operation).filter_by(id=uid).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    total_exp, by_cat = _get_expenses_data(db, uid)
    return _build_financials_out(op.financials, total_exp, by_cat)


@router.put("/{op_id}/financials")
def upsert_financials(
    op_id: str,
    body: FinancialsUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    uid = uuid.UUID(op_id)
    op = db.query(Operation).filter_by(id=uid).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")

    fin = db.query(OperationFinancials).filter_by(operation_id=uid).first()
    if fin is None:
        fin = OperationFinancials(operation_id=uid)
        db.add(fin)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(fin, field, value)
    db.commit()
    db.refresh(fin)

    total_exp, by_cat = _get_expenses_data(db, uid)
    return _build_financials_out(fin, total_exp, by_cat)


@router.delete("/{op_id}", status_code=204)
def delete_operation(
    op_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    op = db.query(Operation).filter_by(id=uuid.UUID(op_id)).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    db.delete(op)
    db.commit()
