from fastapi import APIRouter, Depends

from ..core.database import get_db
from ..schemas import (
    CaptchaResponse,
    RegisterRequest,
    RegisterResponse,
    LoginResponse,
    LoginRequest,
    LogoutResponse,
    SettingsRequest,
    SettingsResponse,
    UserProfileResponse,
)
from ..services.analytics import (
    create_captcha_service,
    get_settings_service,
    get_user_profile_service,
    login_user,
    logout_user,
    register_user,
    save_settings_service,
)
from ..services.auth import get_current_token, get_current_user


router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/register")
def register(request: RegisterRequest, db=Depends(get_db)):
    return register_user(db, request)


@router.post("/captcha", response_model=CaptchaResponse)
def create_captcha():
    return create_captcha_service()


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest, db=Depends(get_db)):
    return login_user(db, request)


@router.post("/logout", response_model=LogoutResponse)
def logout(user=Depends(get_current_user), token=Depends(get_current_token)):
    return logout_user(token)


@router.get("/me", response_model=UserProfileResponse)
def me(user=Depends(get_current_user), db=Depends(get_db)):
    return get_user_profile_service(db, user)


@router.get("/settings", response_model=SettingsResponse)
def get_settings(user=Depends(get_current_user), db=Depends(get_db)):
    return get_settings_service(db, user)


@router.post("/settings", response_model=SettingsResponse)
def save_settings(request: SettingsRequest, user=Depends(get_current_user), db=Depends(get_db)):
    return save_settings_service(db, user, request)
