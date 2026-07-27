from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from .core.config import settings
from .core.redis import redis_delete, redis_get_json, redis_set_json
from .core.security import (
    create_access_token,
    decode_token,
    hash_password,
    token_digest,
    verify_password,
)

SECRET_KEY = settings.jwt_secret_key
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_TTL_SECONDS = settings.access_token_ttl_seconds
USER_CACHE_TTL_SECONDS = settings.user_cache_ttl_seconds
TOKEN_KEY_PREFIX = "auth:token:"
USER_KEY_PREFIX = "user:profile:"


def create_token(data: dict):
    return create_access_token(data, ttl_seconds=ACCESS_TOKEN_TTL_SECONDS)


security = HTTPBearer()


def _token_key(token: str) -> str:
    return f"{TOKEN_KEY_PREFIX}{token_digest(token)}"


def _user_key(user_id: int) -> str:
    return f"{USER_KEY_PREFIX}{user_id}"


def cache_user(user_info: dict, ttl: int = USER_CACHE_TTL_SECONDS):
    redis_set_json(_user_key(user_info["user_id"]), user_info, ttl=ttl)


def get_cached_user(user_id: int) -> dict | None:
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
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    return credentials.credentials


def get_current_user(token: str = Depends(get_current_token)):
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
