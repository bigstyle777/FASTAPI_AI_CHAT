from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from .captcha import _verify_captcha
from .rbac import build_user_context, get_default_role, sync_default_rbac
from ..core import redis
from ..core.config import settings
from ..core.database import get_db
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

security = HTTPBearer()


def create_token(data: dict[str, Any]):
    return create_access_token(data, ttl_seconds=ACCESS_TOKEN_TTL_SECONDS)


def _token_key(token: str) -> str:
    return f"{TOKEN_KEY_PREFIX}{token_digest(token)}"


def _user_key(user_id: int) -> str:
    return f"{USER_KEY_PREFIX}{user_id}"


def cache_user(user_info: dict[str, Any], ttl: int = USER_CACHE_TTL_SECONDS):
    redis.redis_set_json(_user_key(user_info["user_id"]), user_info, ttl=ttl)


def get_cached_user(user_id: int) -> dict[str, Any] | None:
    return redis.redis_get_json(_user_key(user_id))


def create_login_session(user):
    user_info = build_user_context(user)
    token = create_token(
        {
            "user_id": user_info["user_id"],
            "username": user_info["username"],
        }
    )
    redis.redis_set_json(_token_key(token), user_info, ttl=ACCESS_TOKEN_TTL_SECONDS)
    return token


def revoke_login_session(token: str):
    redis.redis_delete(_token_key(token))


def get_current_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> str:
    return credentials.credentials


def resolve_current_user_context(db, token: str) -> dict[str, Any]:
    """校验 Bearer token（JWT 合法 + 会话存在）并返回用户上下文。"""
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    session = redis.redis_get_json(_token_key(token))
    if not session or session.get("user_id") != payload.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        )

    db_user = get_user_by_id(db, session["user_id"])
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_info = build_user_context(db_user)
    redis.redis_set_json(_token_key(token), user_info, ttl=ACCESS_TOKEN_TTL_SECONDS)
    return user_info


def get_current_user(
    request: Request,
    token: Annotated[str, Depends(get_current_token)],
    db = Depends(get_db),
) -> dict[str, Any]:
    current_user = getattr(request.state, "current_user", None)
    if current_user and current_user.get("token") == token:
        return current_user["user"]

    user = resolve_current_user_context(db, token)
    request.state.current_user = {"token": token, "user": user}
    return user


def register_user(db, request):
    sync_default_rbac(db)

    username = request.username.strip()
    password = request.password.strip()
    user = get_user_by_username(db, username)

    if user:
        return {"success": False, "message": "username already exists"}

    hashed_password = hash_password(password)
    default_role = get_default_role(db)
    create_user(db, username, hashed_password, role_id=default_role.id)
    return {"success": True, "message": "registration successful"}


def login_user(db, request):
    if not _verify_captcha(request.captcha_id, request.captcha_code):
        return {"success": False, "message": "invalid captcha"}

    username = request.username.strip()
    password = request.password.strip()
    user = get_user_by_username(db, username)

    if not user:
        return {"success": False, "message": "user not found"}

    if not verify_password(password, user.password):
        return {"success": False, "message": "invalid password"}

    token = create_login_session(user)
    return {
        "success": True,
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
    }


def logout_user(token):
    revoke_login_session(token)
    return {"success": True, "message": "logout successful"}


def get_user_profile_service(db, user):
    db_user = get_user_by_id(db, user["user_id"])
    if not db_user:
        raise BusinessError("user does not exist")

    user_info = build_user_context(db_user)
    return {"success": True, **user_info}
