from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..core.cache import check_rate_limit
from ..core.config import settings
from ..core.database import get_db
from .schemas import (
    CaptchaResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    SettingsRequest,
    SettingsResponse,
    UserProfileResponse,
)
from .services.auth import (
    get_current_token,
    get_current_user,
    get_user_profile_service,
    login_user,
    logout_user,
    register_user,
)
from .services.captcha import create_captcha_service
from .services.settings import get_settings_service, save_settings_service

router = APIRouter(prefix="/users", tags=["Users"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
CurrentToken = Annotated[str, Depends(get_current_token)]
Database = Annotated[Session, Depends(get_db)]

# 认证接口限流配置（防暴力破解）
LOGIN_RATE_LIMIT = 10       # 每 IP 每分钟最多 10 次登录尝试
REGISTER_RATE_LIMIT = 5     # 每 IP 每小时最多 5 次注册
CAPTCHA_RATE_LIMIT = 30     # 每 IP 每分钟最多 30 次验证码请求


def _is_trusted_proxy(client_ip: str) -> bool:
    trusted = settings.trusted_proxy_ips_list
    return "*" in trusted or client_ip in trusted


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP；仅当直连方是可信代理时才采信 X-Forwarded-For。

    默认不信任转发头：否则任何人都能伪造 XFF 换 IP，绕过注册/验证码限流。
    """
    client_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and _is_trusted_proxy(client_ip):
        return forwarded.split(",")[0].strip()
    return client_ip


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
def login(request: Request, response: Response, body: LoginRequest, db: Database):
    _check_auth_rate_limit(request, "login", LOGIN_RATE_LIMIT, 60)
    result = login_user(db, body)
    if result.get("success") and result.get("access_token"):
        # 凭证放 httponly cookie：JS 读不到，XSS 偷不走。secure 按环境自动
        # （production 走 HTTPS 才发，dev 不限）。max_age 与 token TTL 对齐。
        response.set_cookie(
            key=settings.session_cookie_name,
            value=result["access_token"],
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=settings.access_token_ttl_seconds,
            path="/",
        )
    return result


@router.post("/logout", response_model=LogoutResponse)
def logout(response: Response, user: CurrentUser, token: CurrentToken):
    logout_user(token)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"success": True, "message": "logout successful"}


@router.get("/me", response_model=UserProfileResponse)
def me(user: CurrentUser, db: Database):
    return get_user_profile_service(db, user)


@router.get("/settings", response_model=SettingsResponse)
def get_settings(user: CurrentUser, db: Database):
    return get_settings_service(db, user)


@router.post("/settings", response_model=SettingsResponse)
def save_settings(request: SettingsRequest, user: CurrentUser, db: Database):
    return save_settings_service(db, user, request)
