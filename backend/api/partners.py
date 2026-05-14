from __future__ import annotations
import uuid
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import Operation, OperationPartner, OperationStatus
from backend.auth.dependencies import get_current_user, require_admin
from backend.models.operation import User
from backend.api.operations import _build_financials_out, _get_expenses_data

router = APIRouter(prefix="/api/operations", tags=["partners"])


class PartnerCreate(BaseModel):
    name: str
    role: Optional[str] = None
    participation_pct: float
    capital_contributed: Optional[float] = None
    loan_amount: Optional[float] = None
    loan_interest_rate: Optional[float] = None
    loan_months: Optional[int] = None


class PartnerOut(BaseModel):
    id: str
    name: str
    role: Optional[str]
    participation_pct: float
    capital_contributed: Optional[float]
    loan_amount: Optional[float]
    loan_interest_rate: Optional[float]
    loan_months: Optional[int]
    loan_cost: Optional[float]
    roi_pct: Optional[float] = None

    @classmethod
    def from_orm(cls, p: OperationPartner) -> "PartnerOut":
        la  = float(p.loan_amount)        if p.loan_amount        else None
        lir = float(p.loan_interest_rate) if p.loan_interest_rate else None
        lm  = p.loan_months
        cc  = float(p.capital_contributed) if p.capital_contributed else None
        loan_cost: float | None = None
        if la and lir and lm:
            loan_cost = round(la * (lir / 100) * (lm / 12), 2)
        return cls(
            id=str(p.id),
            name=p.name,
            role=p.role,
            participation_pct=float(p.participation_pct),
            capital_contributed=cc,
            loan_amount=la,
            loan_interest_rate=lir,
            loan_months=lm,
            loan_cost=loan_cost,
        )


def _get_op(db: Session, operation_id: str) -> Operation:
    op = db.query(Operation).filter_by(id=uuid.UUID(operation_id)).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    return op


@router.get("/{operation_id}/partners", response_model=list[PartnerOut])
def list_partners(
    operation_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    op = _get_op(db, operation_id)
    return [PartnerOut.from_orm(p) for p in op.op_partners]


@router.post("/{operation_id}/partners", response_model=PartnerOut, status_code=201)
def create_partner(
    operation_id: str,
    body: PartnerCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    op = _get_op(db, operation_id)

    existing_total = sum(float(p.participation_pct) for p in op.op_partners)
    if existing_total + body.participation_pct > 100:
        raise HTTPException(
            status_code=400,
            detail=f"Suma de porcentajes superaría 100% ({existing_total:.1f}% + {body.participation_pct:.1f}%)",
        )

    partner = OperationPartner(
        operation_id=op.id,
        name=body.name,
        role=body.role,
        participation_pct=Decimal(str(body.participation_pct)),
        capital_contributed=Decimal(str(body.capital_contributed)) if body.capital_contributed is not None else None,
        loan_amount=Decimal(str(body.loan_amount)) if body.loan_amount is not None else None,
        loan_interest_rate=Decimal(str(body.loan_interest_rate)) if body.loan_interest_rate is not None else None,
        loan_months=body.loan_months,
    )
    db.add(partner)
    db.commit()
    db.refresh(partner)
    return PartnerOut.from_orm(partner)


@router.delete("/{operation_id}/partners/{partner_id}", status_code=204)
def delete_partner(
    operation_id: str,
    partner_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    uid = uuid.UUID(operation_id)
    pid = uuid.UUID(partner_id)
    p = db.query(OperationPartner).filter_by(id=pid, operation_id=uid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Partner not found")
    db.delete(p)
    db.commit()


@router.get("/{operation_id}/distribution")
def get_distribution(
    operation_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    uid = uuid.UUID(operation_id)
    op = _get_op(db, operation_id)

    if op.status != OperationStatus.vendido:
        return {"available": False, "reason": "Disponible cuando la operación se marque como Vendido"}

    total_exp, by_cat = _get_expenses_data(db, uid)
    fin_data = _build_financials_out(op.financials, total_exp, by_cat)
    net_profit = fin_data.get("net_profit")

    if net_profit is None:
        return {"available": False, "reason": "Beneficio neto no calculable — introduce precio de venta real"}

    total_costes = fin_data.get("total_costes") or 0.0

    items = []
    for p in op.op_partners:
        pct = float(p.participation_pct)
        amount = round(net_profit * pct / 100, 2)
        # capital: always pct/100 * total_costes (capital_contributed in DB is informational only)
        cc = round(pct / 100 * total_costes, 2) if total_costes else None
        la = float(p.loan_amount) if p.loan_amount else 0
        lir = float(p.loan_interest_rate) if p.loan_interest_rate else 0
        lm = p.loan_months or 0
        loan_cost = round(la * (lir / 100) * (lm / 12), 2) if la and lir and lm else 0
        loan_repayment = round(la + loan_cost, 2)
        total_received = round(amount + loan_repayment, 2)
        roi_pct = round(amount / cc * 100, 2) if cc and cc > 0 else None
        items.append({
            "name": p.name,
            "role": p.role or "",
            "participation_pct": pct,
            "capital_contributed": cc,
            "amount": amount,
            "loan_repayment": loan_repayment,
            "total_received": total_received,
            "roi_pct": roi_pct,
        })

    return {
        "available": True,
        "net_profit": net_profit,
        "items": items,
        "total_distributed": round(sum(i["total_received"] for i in items), 2),
    }
