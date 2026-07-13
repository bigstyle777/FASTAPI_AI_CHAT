from fastapi import APIRouter, Depends

from app.database import get_db
from app.schemas import (
    RegisterRequest,
    RegisterResponse,
    LoginResponse,
    LoginRequest,
    SettingsRequest,
    SettingsResponse,
)
from app.services import register_user, login_user, get_settings_service, save_settings_service
from app.auth import get_current_user


router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/register")
def register(request: RegisterRequest, db=Depends(get_db)):
    return register_user(db, request)


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db=Depends(get_db)):
    return login_user(db, request)


@router.get("/me")
def me(user=Depends(get_current_user)):
    return user


@router.get("/settings", response_model=SettingsResponse)
def get_settings(user=Depends(get_current_user), db=Depends(get_db)):
    return get_settings_service(db, user)


@router.post("/settings", response_model=SettingsResponse)
def save_settings(request: SettingsRequest, user=Depends(get_current_user), db=Depends(get_db)):
    return save_settings_service(db, user, request)
