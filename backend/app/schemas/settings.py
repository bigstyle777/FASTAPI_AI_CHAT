from typing import Optional

from pydantic import BaseModel


class SettingsRequest(BaseModel):
    api_key: Optional[str] = None
    provider: str = "deepseek"
    embedding_api_key: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_model: Optional[str] = None


class SettingsResponse(BaseModel):
    success: bool
    api_key: Optional[str] = None
    provider: str = "deepseek"
    embedding_api_key: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_model: Optional[str] = None
