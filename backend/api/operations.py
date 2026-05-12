from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import Operation, OperationStatus
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
    return [OperationOut.from_orm(op) for op in ops]


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
