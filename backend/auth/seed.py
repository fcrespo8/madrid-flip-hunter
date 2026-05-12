"""
Creates the first admin user.
Usage: poetry run python -m backend.auth.seed
Reads ADMIN_USERNAME and ADMIN_PASSWORD from env, falls back to dev defaults.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from backend.models.database import SessionLocal
from backend.models.operation import User, UserRole
from backend.auth.security import hash_password


def seed_admin() -> None:
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin1234")

    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(username=username).first()
        if existing:
            print(f"Admin user '{username}' already exists — skipping.")
            return
        user = User(
            username=username,
            hashed_password=hash_password(password),
            role=UserRole.admin,
        )
        db.add(user)
        db.commit()
        print(f"Admin user '{username}' ready.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
