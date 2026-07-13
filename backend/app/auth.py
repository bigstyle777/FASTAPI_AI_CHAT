import os
import bcrypt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "myselectkey")
ALGORITHM = "HS256"


def create_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str):
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


security = HTTPBearer()


def get_current_user(token=Depends(security)):
    try:
        payload = decode_token(token.credentials)
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="INVALID TOKEN",
            headers={"WWW-Authenticate": "Bearer"},
        )


def hash_password(password: str):
    password_bytes = password.encode()
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode()


def verify_password(password: str, hashed_password: str):
    password_bytes = password.encode()
    hashed_bytes = hashed_password.encode()
    return bcrypt.checkpw(password_bytes, hashed_bytes)
