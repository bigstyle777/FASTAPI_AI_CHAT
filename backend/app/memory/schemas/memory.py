from typing import Optional

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=2000,
        description="记忆内容",
    )


class MemoryUpdateRequest(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=2000,
        description="新的记忆内容",
    )


class MemoryResponse(BaseModel):
    memory_id: int
    content: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MemoryListResponse(BaseModel):
    success: bool
    memories: list[MemoryResponse]


class MemoryMutationResponse(BaseModel):
    success: bool
    memory: MemoryResponse
