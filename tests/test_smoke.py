import os
os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


def test_listing_price_per_m2():
    from backend.models.listing import Listing
    l = Listing()
    l.price = 200000.0
    l.size_m2 = 50.0
    assert l.price_per_m2() == 4000.0


def test_listing_price_per_m2_sin_tamanyo():
    from backend.models.listing import Listing
    l = Listing()
    l.price = 200000.0
    l.size_m2 = None
    assert l.price_per_m2() is None


def test_score_tool_estructura():
    from backend.agents.scoring_agent import SCORE_TOOL
    props = SCORE_TOOL["input_schema"]["properties"]
    assert "score" in props
    assert "reasoning" in props
    assert "red_flags" in props
    assert "green_flags" in props


def test_score_tool_campos_requeridos():
    from backend.agents.scoring_agent import SCORE_TOOL
    required = set(SCORE_TOOL["input_schema"]["required"])
    assert required == {"score", "reasoning", "red_flags", "green_flags"}


def test_auth_token_schema():
    from backend.auth.security import create_access_token, decode_token
    token = create_access_token({"sub": "testuser"})
    payload = decode_token(token)
    assert payload["sub"] == "testuser"


def test_operations_crud_schema():
    from backend.api.operations import OperationCreate
    op = OperationCreate(name="Test", status="prospecto")
    assert op.status == "prospecto"


def test_financials_calculated_fields():
    from backend.api.operations import FinancialsUpdate
    f = FinancialsUpdate(purchase_price=100000, purchase_taxes=10000, purchase_notary=2000)
    assert f is not None
    assert f.purchase_price == 100000
    assert f.purchase_taxes == 10000
    assert f.purchase_notary == 2000


def test_expense_create_schema():
    from backend.api.expenses import ExpenseCreate
    expense = ExpenseCreate(
        date="2026-01-01",
        description="Reforma baño",
        category="reforma",
        amount=5000.0,
        paid_by="francisco",
    )
    assert expense.amount == 5000.0
