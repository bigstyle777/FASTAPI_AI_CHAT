from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 找项目路径
APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            PROJECT_ROOT / ".env",
            BACKEND_DIR / ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str | None = None
    ai_model: str | None = None
    jwt_secret_key: str = "myselectkey"
    jwt_algorithm: str = "HS256"
    redis_url: str = "redis://127.0.0.1:6379/0"
    access_token_ttl_seconds: int = 60 * 60 * 24 * 30
    captcha_ttl_seconds: int = 300
    user_cache_ttl_seconds: int = 60 * 60 * 24
    user_settings_cache_ttl_seconds: int = 3600
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aichat"
    chat_ttl_seconds: int = 60 * 60
    stop_generation_ttl_seconds: int = 60 * 5

    @model_validator(mode="after")
    def fill_derived_defaults(self):
        if not self.openai_model:
            self.openai_model = self.ai_model or "gpt-4o-mini"
        if not self.deepseek_model:
            self.deepseek_model = self.ai_model or "deepseek-v4-flash"
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
