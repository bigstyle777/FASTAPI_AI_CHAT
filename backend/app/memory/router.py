"""记忆页的 CRUD 接口：/memories。

这一层只做参数校验和序列化，业务规则都在 repository / manager 里。
"""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..chat.schemas import ActionResponse
from ..core.database import get_db
from ..core.exceptions import BusinessError
from ..task.memory_queue import enqueue_consolidation, enqueue_decay
from ..user.services.auth import get_current_user
from . import config as memory_config
from .repository import (
    create_episodic,
    delete_memory,
    find_duplicate_pairs,
    get_by_id,
    list_by_user,
    update_content,
    upsert_semantic,
)
from .schemas import (
    DuplicateListResponse,
    MemoryCreateRequest,
    MemoryDuplicate,
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


def _pick_keep(left, right):
    """疑似重复的两条里建议保留哪条：importance 高的优先，打平时留最近改过的。

    这只是建议，不是决定 —— 机器分不清"用 X"和"不用 X"，别替用户拍板。
    updated_at 理论上不会是空（列有 NOT NULL），但手动构造的对象可能没有，
    兜个底比让接口 500 划算。
    """
    left_rank = (left.importance, left.updated_at or datetime.min)
    right_rank = (right.importance, right.updated_at or datetime.min)
    return left if left_rank >= right_rank else right


@router.get("", response_model=MemoryListResponse)
def list_memories(user: CurrentUser, db: Database):
    """列出当前用户的全部记忆（semantic + episodic，最新的在前）。"""
    memories = list_by_user(db, user["user_id"])
    return {
        "success": True,
        "memories": [_serialize(memory) for memory in memories],
    }


@router.get("/duplicates", response_model=DuplicateListResponse)
def list_duplicates(
    user: CurrentUser,
    db: Database,
    threshold: float | None = None,
):
    """列出语义高度接近的 semantic 记忆对（疑似同一个槽位存成了两条）。

    key 由提取时的 LLM 生成，同一个槽位可能被写成 backend_framework 和
    backend_stack 两个 key —— 那样两条会长期并存，检索时一起被召回，
    模型看到的是自相矛盾的上下文。这里只把可疑的对子挑出来给人看，
    不自动合并：相似不等于同义（"用 X" 和 "不用 X" 向量极近）。

    合并动作由用户走已有的修改 / 删除接口完成。
    """
    used_threshold = (
        memory_config.DUPLICATE_SIMILARITY_THRESHOLD
        if threshold is None
        else threshold
    )
    pairs = find_duplicate_pairs(db, user["user_id"], threshold=used_threshold)

    duplicates = []
    for left, right, similarity in pairs:
        keep = _pick_keep(left, right)
        drop = right if keep is left else left
        duplicates.append(
            MemoryDuplicate(
                similarity=round(similarity, 4),
                keep=_serialize(keep),
                drop=_serialize(drop),
            )
        )

    return {
        "success": True,
        "threshold": used_threshold,
        "duplicates": duplicates,
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


@router.post("/decay", response_model=ActionResponse)
def trigger_decay(user: CurrentUser):
    """手动触发一次生命周期清扫（过期归档 / 冷落降级 / 观察窗归档）。

    与合并一样异步执行：本接口只入队，实际清理在 Celery worker 上跑。
    """
    if not enqueue_decay(user["user_id"]):
        raise BusinessError("清理任务提交失败，请确认后台任务服务已启动后重试")
    return {"success": True, "message": "记忆清理任务已提交，完成后旧的记忆会被归档"}


@router.delete("/{memory_id}", response_model=ActionResponse)
def remove_memory(memory_id: int, user: CurrentUser, db: Database):
    """删除一条记忆。"""
    if not delete_memory(db, memory_id, user["user_id"]):
        raise BusinessError("记忆不存在")
    return {"success": True, "message": "记忆已删除"}
