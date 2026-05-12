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
