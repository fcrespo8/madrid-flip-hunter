from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import User, UserRole
from backend.auth.security import verify_password, hash_password, create_access_token
from backend.auth.dependencies import get_current_user, require_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


@router.post("/token")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(username=form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "username": current_user.username,
        "role": current_user.role.value,
    }


@router.post("/users")
def create_user(
    body: CreateUserRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if db.query(User).filter_by(username=body.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    try:
        role = UserRole(body.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {body.role}")
    user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": str(user.id), "username": user.username, "role": user.role.value}
