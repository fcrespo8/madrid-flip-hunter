from __future__ import annotations
import uuid
from datetime import date as date_type
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import Operation, OperationExpense, ExpenseCategory, PaidBy
from backend.auth.dependencies import get_current_user, require_admin
from backend.models.operation import User

router = APIRouter(prefix="/api/operations", tags=["expenses"])


class ExpenseCreate(BaseModel):
    date: str
    description: str
    category: str
    amount: float
    paid_by: str
    payment_method: Optional[str] = None
    notes: Optional[str] = None


class ExpenseOut(BaseModel):
    id: str
    date: str
    description: str
    category: str
    amount: float
    paid_by: str
    payment_method: Optional[str]
    notes: Optional[str]
    created_at: str

    @classmethod
    def from_orm(cls, e: OperationExpense) -> "ExpenseOut":
        return cls(
            id=str(e.id),
            date=str(e.date),
            description=e.description,
            category=e.category.value,
            amount=float(e.amount),
            paid_by=e.paid_by.value,
            payment_method=e.payment_method,
            notes=e.notes,
            created_at=e.created_at.isoformat(),
        )


def _parse_enum(enum_cls, value: str, field: str):
    try:
        return enum_cls(value)
    except ValueError:
        valid = [e.value for e in enum_cls]
        raise HTTPException(status_code=400, detail=f"Invalid {field} '{value}'. Valid: {valid}")


@router.get("/{operation_id}/expenses")
def list_expenses(
    operation_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    uid = uuid.UUID(operation_id)
    if not db.query(Operation).filter_by(id=uid).first():
        raise HTTPException(status_code=404, detail="Operation not found")
    expenses = (
        db.query(OperationExpense)
        .filter_by(operation_id=uid)
        .order_by(OperationExpense.date.desc(), OperationExpense.created_at.desc())
        .all()
    )
    summary: dict[str, float] = {
        "obra": 0.0, "tramites": 0.0, "comunidad_otros": 0.0,
        "agencia_compra": 0.0, "agencia_venta": 0.0,
        "impuestos": 0.0, "financiacion": 0.0, "total": 0.0,
    }
    for e in expenses:
        amt = float(e.amount)
        cat = e.category
        desc = (e.description or "").lower()
        summary["total"] += amt
        if cat in (ExpenseCategory.reforma, ExpenseCategory.reforma_extra):
            summary["obra"] += amt
        elif cat == ExpenseCategory.honorarios:
            summary["tramites"] += amt
        elif cat in (ExpenseCategory.comunidad, ExpenseCategory.suministros, ExpenseCategory.otros):
            summary["comunidad_otros"] += amt
        elif cat == ExpenseCategory.agencia:
            if any(kw in desc for kw in ("compra", "inmobiliaria")):
                summary["agencia_compra"] += amt
            else:
                summary["agencia_venta"] += amt
        elif cat == ExpenseCategory.impuestos:
            summary["impuestos"] += amt
        elif cat == ExpenseCategory.financiacion:
            summary["financiacion"] += amt
    summary = {k: round(v, 2) for k, v in summary.items()}
    return {
        "expenses": [ExpenseOut.from_orm(e) for e in expenses],
        "summary": summary,
    }


@router.post("/{operation_id}/expenses", response_model=ExpenseOut, status_code=201)
def create_expense(
    operation_id: str,
    body: ExpenseCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    uid = uuid.UUID(operation_id)
    if not db.query(Operation).filter_by(id=uid).first():
        raise HTTPException(status_code=404, detail="Operation not found")

    category = _parse_enum(ExpenseCategory, body.category, "category")
    paid_by  = _parse_enum(PaidBy, body.paid_by, "paid_by")

    try:
        parsed_date = date_type.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    expense = OperationExpense(
        operation_id=uid,
        date=parsed_date,
        description=body.description,
        category=category,
        amount=body.amount,
        paid_by=paid_by,
        payment_method=body.payment_method,
        notes=body.notes,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return ExpenseOut.from_orm(expense)


@router.delete("/{operation_id}/expenses/{expense_id}", status_code=204)
def delete_expense(
    operation_id: str,
    expense_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    uid = uuid.UUID(operation_id)
    eid = uuid.UUID(expense_id)
    expense = db.query(OperationExpense).filter_by(id=eid, operation_id=uid).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    db.delete(expense)
    db.commit()
