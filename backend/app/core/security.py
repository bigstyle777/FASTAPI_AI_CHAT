import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import bcrypt
from jose import jwt

from .config import settings


def hash_password(password: str) -> str:
    password_bytes = password.encode()
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode()


def verify_password(password: str, hashed_password: str) -> bool:
    password_bytes = password.encode()
    hashed_bytes = hashed_password.encode()
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def create_access_token(data: dict, ttl_seconds: int | None = None) -> str:
    expire_seconds = (
        settings.access_token_ttl_seconds if ttl_seconds is None else ttl_seconds
    )
    expire = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)

    to_encode = data.copy()
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4())})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def token_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()
