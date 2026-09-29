from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

PRODUCTION_DB_MARKER = "rlwy.net"


class ProductionDatabaseError(RuntimeError):
    """Se intentó usar la DB de producción (Railway) fuera de producción."""


def check_database_url(url: str | None, env: str | None) -> None:
    """Corta si la URL apunta a Railway y ENV no es 'production'. Evita que un
    script o test local escriba en producción por heredar el DATABASE_URL del .env."""
    if url and PRODUCTION_DB_MARKER in url and env != "production":
        raise ProductionDatabaseError(
            f"DATABASE_URL apunta a producción ({PRODUCTION_DB_MARKER}) pero ENV={env!r}. "
            "Usá una DB local, o definí ENV=production si de verdad corresponde."
        )


check_database_url(DATABASE_URL, os.getenv("ENV"))

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=3,          # default 5 — reducido para Railway (no hay tráfico concurrente alto)
    max_overflow=5,       # default 10 — tope de conexiones extra bajo pico
    pool_recycle=300,     # reciclar conexiones cada 5 min (evita stale connections en Railway)
    pool_pre_ping=True,   # validar conexión antes de usarla del pool
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Import all models so Alembic autogenerate can detect them
from backend.models import listing, operation  # noqa: E402, F401
