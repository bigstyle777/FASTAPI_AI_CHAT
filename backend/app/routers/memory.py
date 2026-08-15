from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..exceptions import BusinessError
from ..schemas import (
    ActionResponse,
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryMutationResponse,
    MemoryResponse,
    MemoryUpdateRequest,
)
from ..services.auth import get_current_user
from ..services.memory import (
    create_memory_service,
    delete_memory_service,
    get_memories_service,
    update_memory_service,
)

router = APIRouter(prefix="/memories", tags=["Memories"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


def _format_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds")


def _serialize_memory(memory) -> MemoryResponse:
    return MemoryResponse(
        memory_id=memory.id,
        content=memory.content,
        created_at=_format_dt(memory.created_at),
        updated_at=_format_dt(memory.updated_at),
    )


@router.get("", response_model=MemoryListResponse)
def list_memories(user: CurrentUser, db: Database):
    """查看当前用户的记忆列表。"""
    memories = get_memories_service(db, user["user_id"])
    return {
        "success": True,
        "memories": [_serialize_memory(memory) for memory in memories],
    }


@router.post("", response_model=MemoryMutationResponse)
def create_memory(request: MemoryCreateRequest, user: CurrentUser, db: Database):
    """新增一条记忆（保存后自动入队向量化）。"""
    try:
        memory = create_memory_service(db, user["user_id"], request.content)
    except ValueError as error:
        raise BusinessError(str(error))
    return {"success": True, "memory": _serialize_memory(memory)}


@router.put("/{memory_id}", response_model=MemoryMutationResponse)
def update_memory(
    memory_id: int,
    request: MemoryUpdateRequest,
    user: CurrentUser,
    db: Database,
):
    """修改记忆内容；旧向量作废并自动重新向量化。"""
    try:
        memory = update_memory_service(
            db,
            user["user_id"],
            memory_id,
            request.content,
        )
    except ValueError as error:
        raise BusinessError(str(error))
    return {"success": True, "memory": _serialize_memory(memory)}


@router.delete("/{memory_id}", response_model=ActionResponse)
def delete_memory(memory_id: int, user: CurrentUser, db: Database):
    """删除记忆，同时清理 user_memories 和 user_memory_embeddings 两张表。"""
    try:
        delete_memory_service(db, user["user_id"], memory_id)
    except ValueError as error:
        raise BusinessError(str(error))
    return {"success": True, "message": "Memory 已删除"}
