from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import User, UserRole
from backend.auth.security import decode_token, APP_USERNAME

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


class _EnvUser:
    """Lightweight stand-in for the env-var admin — avoids a DB round-trip."""
    def __init__(self, username: str):
        self.username = username
        self.role = UserRole.admin
        self.id = None


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    # Env-var admin — skip DB entirely
    if username == APP_USERNAME:
        return _EnvUser(username)
    user = db.query(User).filter_by(username=username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(current_user=Depends(get_current_user)):
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
