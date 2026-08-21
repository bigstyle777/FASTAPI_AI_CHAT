from typing import Optional

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=50)


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha_id: str = Field(min_length=1)
    captcha_code: str = Field(min_length=1, max_length=10)


class CaptchaResponse(BaseModel):
    success: bool
    captcha_id: str
    image: str
    expires_in: int


class RegisterResponse(BaseModel):
    success: bool
    message: str


class LoginResponse(BaseModel):
    success: bool
    access_token: str = ""
    token_type: str = "bearer"
    expires_in: int = 0
    message: Optional[str] = None


class LogoutResponse(BaseModel):
    success: bool
    message: str


class UserProfileResponse(BaseModel):
    success: bool
    user_id: int
    username: str
    role: str = "user"
    permissions: list[str] = Field(default_factory=list)
