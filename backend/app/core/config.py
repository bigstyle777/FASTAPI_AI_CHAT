from functools import lru_cache
import os
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
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    access_token_ttl_seconds: int = 60 * 60 * 24 * 30
    captcha_ttl_seconds: int = 300
    user_cache_ttl_seconds: int = 60 * 60 * 24
    user_settings_cache_ttl_seconds: int = 3600
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aichat"
    chat_ttl_seconds: int = 60 * 60
    stop_generation_ttl_seconds: int = 60 * 5
    bootstrap_admin_username: str | None = "admin"
    bootstrap_admin_password: str | None = "admin123456"
    rag_enabled: bool = True
    rag_upload_dir: str = "backend/uploads/rag"
    rag_embedding_api_key: str | None = None
    rag_embedding_base_url: str = "https://api.siliconflow.cn/v1"
    rag_embedding_model: str = "BAAI/bge-large-zh-v1.5"
    rag_embedding_dimension: int = 1024
    rag_top_k: int = 5
    rag_chunk_size: int = 900
    rag_chunk_overlap: int = 150
    rag_max_context_chars: int = 5000
    tavily_api_key: str | None = None
    log_level: str = "INFO"

    @model_validator(mode="after")
    def fill_derived_defaults(self):
        # Docker secrets 惯例：<FIELD>_FILE 指向只读文件时，优先读取文件内容作为密钥。
        # 容器内由 compose 挂载 ./secrets -> /run/secrets 并注入 *_KEY_FILE 变量，
        # 避免密钥出现在环境变量（docker inspect 可见）；文件缺失时回退 env_file 注入。
        for field in ("deepseek_api_key", "openai_api_key", "tavily_api_key", "rag_embedding_api_key"):
            if getattr(self, field) is None:
                file_env = os.environ.get(f"{field.upper()}_FILE")
                if file_env:
                    try:
                        content = Path(file_env).read_text(encoding="utf-8").strip()
                        if content:
                            setattr(self, field, content)
                    except OSError:
                        pass
        if not self.openai_model:
            self.openai_model = self.ai_model or "gpt-4o-mini"
        if not self.deepseek_model:
            self.deepseek_model = self.ai_model or "deepseek-v4-flash"
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """将逗号分隔的 CORS 允许来源字符串解析为列表。"""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def is_default_jwt_secret(self) -> bool:
        """检测 JWT 密钥是否为不安全的默认值。"""
        return self.jwt_secret_key in ("myselectkey", "change-me", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
