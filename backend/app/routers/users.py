from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas import (
    CaptchaResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    SettingsRequest,
    SettingsResponse,
    UserProfileResponse,
)
from ..services.auth import (
    get_current_token,
    get_current_user,
    get_user_profile_service,
    login_user,
    logout_user,
    register_user,
)
from ..services.captcha import create_captcha_service
from ..services.settings import get_settings_service, save_settings_service

router = APIRouter(prefix="/users", tags=["Users"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
CurrentToken = Annotated[str, Depends(get_current_token)]
Database = Annotated[Session, Depends(get_db)]


@router.post("/register")
def register(request: RegisterRequest, db: Database):
    return register_user(db, request)


@router.post("/captcha", response_model=CaptchaResponse)
def create_captcha():
    return create_captcha_service()


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db: Database):
    return login_user(db, request)


@router.post("/logout", response_model=LogoutResponse)
def logout(user: CurrentUser, token: CurrentToken):
    return logout_user(token)


@router.get("/me", response_model=UserProfileResponse)
def me(user: CurrentUser, db: Database):
    return get_user_profile_service(db, user)


@router.get("/settings", response_model=SettingsResponse)
def get_settings(user: CurrentUser, db: Database):
    return get_settings_service(db, user)


@router.post("/settings", response_model=SettingsResponse)
def save_settings(request: SettingsRequest, user: CurrentUser, db: Database):
    return save_settings_service(db, user, request)
