import os
import pytest
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from datetime import date
from decimal import Decimal


def _has_real_db() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    return bool(url) and "test@localhost" not in url


@pytest.mark.skipif(not _has_real_db(), reason="No real DB available")
def test_operation_with_expense():
    from backend.models.database import SessionLocal
    from backend.models.operation import Operation, OperationExpense, OperationStatus, ExpenseCategory, PaidBy

    db = SessionLocal()
    op_id = None
    try:
        op = Operation(name="Test Lavapiés 42", status=OperationStatus.prospecto)
        db.add(op)
        db.flush()
        op_id = op.id

        expense = OperationExpense(
            operation_id=op.id,
            date=date.today(),
            description="Reforma baño",
            category=ExpenseCategory.reforma,
            amount=Decimal("1000.00"),
            paid_by=PaidBy.francisco,
        )
        db.add(expense)
        db.commit()

        fetched = db.query(OperationExpense).filter_by(operation_id=op.id).first()
        assert fetched.amount == Decimal("1000.00")

    finally:
        if op_id:
            db.query(OperationExpense).filter_by(operation_id=op_id).delete()
            db.query(Operation).filter_by(id=op_id).delete()
            db.commit()
        db.close()
