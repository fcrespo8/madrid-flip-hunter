import logging
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext

# Accept JWT_SECRET (new name) or SECRET_KEY (legacy), fall back to insecure default
SECRET_KEY = os.environ.get("JWT_SECRET") or os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
VIEWER_USERNAME = os.environ.get("VIEWER_USERNAME", "viewer")
VIEWER_PASSWORD = os.environ.get("VIEWER_PASSWORD", "viewer")

if ADMIN_USERNAME == "admin" and ADMIN_PASSWORD == "admin":
    logging.warning(
        "⚠️  ADMIN_USERNAME/ADMIN_PASSWORD are using insecure defaults. "
        "Set ADMIN_USERNAME, ADMIN_PASSWORD and JWT_SECRET in your environment."
    )

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
