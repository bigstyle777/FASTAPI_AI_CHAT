from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from ..services.cache import check_rate_limit
from ..services.captcha import create_captcha_service
from ..services.settings import get_settings_service, save_settings_service

router = APIRouter(prefix="/users", tags=["Users"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
CurrentToken = Annotated[str, Depends(get_current_token)]
Database = Annotated[Session, Depends(get_db)]

# 认证接口限流配置（防暴力破解）
LOGIN_RATE_LIMIT = 10       # 每 IP 每分钟最多 10 次登录尝试
REGISTER_RATE_LIMIT = 5     # 每 IP 每小时最多 5 次注册
CAPTCHA_RATE_LIMIT = 30     # 每 IP 每分钟最多 30 次验证码请求


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP，支持代理转发场景（X-Forwarded-For）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_auth_rate_limit(request: Request, endpoint: str, limit: int, expire_seconds: int):
    """认证接口限流检查，超限返回 429。"""
    client_ip = _get_client_ip(request)
    allowed = check_rate_limit(
        key=f"rate_limit:{endpoint}:{client_ip}",
        limit=limit,
        expire_seconds=expire_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后再试",
        )


@router.post("/register")
def register(request: Request, body: RegisterRequest, db: Database):
    _check_auth_rate_limit(request, "register", REGISTER_RATE_LIMIT, 3600)
    return register_user(db, body)


@router.post("/captcha", response_model=CaptchaResponse)
def create_captcha(request: Request):
    _check_auth_rate_limit(request, "captcha", CAPTCHA_RATE_LIMIT, 60)
    return create_captcha_service()


@router.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest, db: Database):
    _check_auth_rate_limit(request, "login", LOGIN_RATE_LIMIT, 60)
    return login_user(db, body)


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
