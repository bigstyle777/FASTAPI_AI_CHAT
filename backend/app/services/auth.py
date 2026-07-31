from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from .captcha import _verify_captcha
from ..core.config import settings
from ..core.redis import redis_delete, redis_get_json, redis_set_json
from ..core.security import (
    create_access_token,
    decode_token,
    hash_password,
    token_digest,
    verify_password,
)
from ..crud import create_user, get_user_by_id, get_user_by_username
from ..exceptions import BusinessError

SECRET_KEY = settings.jwt_secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_TTL_SECONDS = settings.access_token_ttl_seconds
USER_CACHE_TTL_SECONDS = settings.user_cache_ttl_seconds
TOKEN_KEY_PREFIX = "auth:token:"
USER_KEY_PREFIX = "user:profile:"


def create_token(data: dict[str, Any]):
    return create_access_token(data, ttl_seconds=ACCESS_TOKEN_TTL_SECONDS)


security = HTTPBearer()


def _token_key(token: str) -> str:
    return f"{TOKEN_KEY_PREFIX}{token_digest(token)}"


def _user_key(user_id: int) -> str:
    return f"{USER_KEY_PREFIX}{user_id}"


def cache_user(user_info: dict[str, Any], ttl: int = USER_CACHE_TTL_SECONDS):
    redis_set_json(_user_key(user_info["user_id"]), user_info, ttl=ttl)


def get_cached_user(user_id: int) -> dict[str, Any] | None:
    return redis_get_json(_user_key(user_id))


def create_login_session(user):
    user_info = {"user_id": user.id, "username": user.username}
    token = create_token(user_info)
    redis_set_json(_token_key(token), user_info, ttl=ACCESS_TOKEN_TTL_SECONDS)
    cache_user(user_info)
    return token


def revoke_login_session(token: str):
    redis_delete(_token_key(token))


def get_current_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> str:
    return credentials.credentials


def get_current_user(token: Annotated[str, Depends(get_current_token)]) -> dict[str, Any]:
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = redis_get_json(_token_key(token))
    if not user or user.get("user_id") != payload.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def register_user(db, request):
    username = request.username.strip()
    password = request.password.strip()
    user = get_user_by_username(db, username)

    if user:
        return {"success": False, "message": "用户名已存在"}

    hashed_password = hash_password(password)
    create_user(db, username, hashed_password)
    return {"success": True, "message": "注册成功"}


def login_user(db, request):
    if not _verify_captcha(request.captcha_id, request.captcha_code):
        return {"success": False, "message": "验证码错误或已过期"}

    username = request.username.strip()
    password = request.password.strip()
    user = get_user_by_username(db, username)

    if not user:
        return {"success": False, "message": "用户不存在"}

    if not verify_password(password, user.password):
        return {"success": False, "message": "密码错误"}

    token = create_login_session(user)
    return {
        "success": True,
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
    }


def logout_user(token):
    revoke_login_session(token)
    return {"success": True, "message": "退出登录成功"}


def get_user_profile_service(db, user):
    cached_user = get_cached_user(user["user_id"])
    if cached_user:
        return {"success": True, **cached_user}

    db_user = get_user_by_id(db, user["user_id"])
    if not db_user:
        raise BusinessError("用户不存在")

    user_info = {"user_id": db_user.id, "username": db_user.username}
    cache_user(user_info)
    return {"success": True, **user_info}
