"""记忆页的 CRUD 接口：/memories。

这一层只做参数校验和序列化，业务规则都在 repository / manager 里。
"""

from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..chat.schemas import ActionResponse
from ..core.database import get_db
from ..core.exceptions import BusinessError
from ..task.memory_queue import enqueue_consolidation
from ..user.services.auth import get_current_user
from .repository import (
    create_episodic,
    delete_memory,
    get_by_id,
    list_by_user,
    update_content,
    upsert_semantic,
)
from .schemas import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryMutationResponse,
    MemoryRead,
    MemoryType,
    MemoryUpdateRequest,
)

router = APIRouter(prefix="/memories", tags=["Memories"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


def _auto_key(value: str) -> str:
    """用户没填 key 时，按内容生成一个稳定 key。

    同一段内容永远得到同一个 key，重复保存会覆盖而不是堆两条。
    """
    digest = sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"note_{digest}"


def _serialize(memory) -> MemoryRead:
    return MemoryRead.model_validate(memory)


@router.get("", response_model=MemoryListResponse)
def list_memories(user: CurrentUser, db: Database):
    """列出当前用户的全部记忆（semantic + episodic，最新的在前）。"""
    memories = list_by_user(db, user["user_id"])
    return {
        "success": True,
        "memories": [_serialize(memory) for memory in memories],
    }


@router.get("/{memory_id}", response_model=MemoryMutationResponse)
def get_memory(memory_id: int, user: CurrentUser, db: Database):
    """按 id 取一条记忆。"""
    memory = get_by_id(db, memory_id, user["user_id"])
    if memory is None:
        raise BusinessError("记忆不存在")
    return {"success": True, "memory": _serialize(memory)}


@router.post("", response_model=MemoryMutationResponse)
def create_memory(request: MemoryCreateRequest, user: CurrentUser, db: Database):
    """手动新增一条记忆。

    semantic 需要 value（key 可留空，会自动生成）；episodic 需要 summary。
    """
    user_id = user["user_id"]

    if request.type == MemoryType.SEMANTIC:
        if not (request.value or "").strip():
            raise BusinessError("语义记忆的内容不能为空")
        memory = upsert_semantic(
            db,
            user_id,
            (request.key or "").strip() or _auto_key(request.value.strip()),
            request.value.strip(),
            importance=request.importance,
            source="user_input",
        )
    else:
        if not (request.summary or "").strip():
            raise BusinessError("事件记忆的摘要不能为空")
        memory = create_episodic(
            db,
            user_id,
            request.summary.strip(),
            event_type=request.event_type,
            importance=request.importance,
            source="user_input",
        )

    return {"success": True, "memory": _serialize(memory)}


@router.put("/{memory_id}", response_model=MemoryMutationResponse)
def update_memory(
    memory_id: int,
    request: MemoryUpdateRequest,
    user: CurrentUser,
    db: Database,
):
    """修改记忆正文。episodic 改了正文后旧向量作废，需要等下次回填。"""
    memory = update_content(db, memory_id, user["user_id"], request.content.strip())
    if memory is None:
        raise BusinessError("记忆不存在")
    return {"success": True, "memory": _serialize(memory)}


@router.post("/consolidate", response_model=ActionResponse)
def trigger_consolidation(user: CurrentUser):
    """手动触发一次 episodic 合并（重复事件归并为高层总结）。

    实际工作在 Celery worker 上异步跑，这里只负责入队；
    入队失败（worker 未启动等）返回业务错误而不是假成功。
    """
    if not enqueue_consolidation(user["user_id"]):
        raise BusinessError("合并任务提交失败，请确认后台任务服务已启动后重试")
    return {"success": True, "message": "记忆合并任务已提交，完成后可在列表看到结果"}


@router.delete("/{memory_id}", response_model=ActionResponse)
def remove_memory(memory_id: int, user: CurrentUser, db: Database):
    """删除一条记忆。"""
    if not delete_memory(db, memory_id, user["user_id"]):
        raise BusinessError("记忆不存在")
    return {"success": True, "message": "记忆已删除"}
