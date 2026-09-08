"""记忆生命周期（Phase 6）：Reinforce / Decay / Archive。

三种"时间"动作，谁也别和谁混：
- retrieve 命中一条记忆只刷新 last_accessed_at（touch_accessed）——
  那只是"被看到"；
- reinforce 是"确认被真正用上"（这条记忆被拼进了即将喂给模型的 prompt），
  才累加 access_count、把退化中的记忆拉回来；
- run_decay 是定期清扫，把长期没用的低价值记忆降级、再归档。

reinforce 刻意不动 confidence："跟当前问题相关"不等于"没记错"，让它自动
涨分会形成自我强化的回路（详见 config.REINFORCE_ACCESS_THRESHOLD 上的说明）。

状态机（任何状态都不物理删除）：
    ACTIVE ──reinforce 累计达阈值──> REINFORCED
    ACTIVE ──长期未访问且低 importance──> DECAYING
    DECAYING ──观察窗内被 reinforce──> 拉回 ACTIVE（降级拉回）
    DECAYING ──观察窗过仍无人访问──> ARCHIVED
    任意状态 ──expires_at 到期──> ARCHIVED
    检索默认排除 ARCHIVED 与 CONSOLIDATED（见 repository 的过滤）

REINFORCED 换来的不是"排得更前"，而是"免于被冷落降级"
------------------------------------------------------
强化信号刻意不进排序：access_count 只说明这条"跟当前问题相关"，不说明
它没记错；让它加分就是"越常被召回 → 分越高 → 更容易被召回"的自我强化
回路，一条错的记忆会再也翻不了身。而且 reinforce 已经顺带刷新了
last_accessed_at，recency 因子早就涵盖了"最近常用"这层意思，再加一次
是重复计分。

所以 REINFORCED 只影响**留存**：它被排除在冷落降级之外（见 repository
的 list_stale_memories），别的一概不变。"被反复用上过"是"不该被清理"
的证据，不是"该排第一"的证据。

与 Consolidation 一样不注册定时器：run_decay 由 Celery 手动任务
触发（见 task/memory_*）。归档是"不再返回"，不是"删除"，随时可以
靠直接查库找回 —— 这是刻意为之：宁可留着占一点空间，也不冒丢失
用户长期记忆的风险。
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import config
from .repository import (
    list_decaying_memories,
    list_expired_memories,
    list_memories_to_reinforce,
    list_stale_memories,
)
from .status import ACTIVE, ARCHIVED, DECAYING, REINFORCED

logger = logging.getLogger(__name__)


def reinforce_memories(
    db: Session,
    user_id: int,
    memory_ids: list[int],
    *,
    now: datetime | None = None,
) -> int:
    """确认一批记忆被真正用上，返回实际强化的条数。

    对每条"还活着"（非 ARCHIVED/CONSOLIDATED）的记忆：
      - access_count + 1，last_accessed_at 刷新到 now
      - 状态迁移：
          DECAYING  → 先拉回 ACTIVE（"降级拉回"：又被用上了就不该归档）
          access_count 达 REINFORCE_ACCESS_THRESHOLD → 提升为 REINFORCED

    confidence 不在这里改（原因见 config.REINFORCE_ACCESS_THRESHOLD 的注释）。

    调用方如果已经知道这批记忆即将进 prompt，应该让检索层跳过
    touch_accessed（retrieve_memories(..., mark_accessed=False)），否则会
    对同一批 id 写两次库 —— 本函数已经把 last_accessed_at 一起刷了。

    单事务提交；失败整体回滚（强化是低频操作，不需要部分成功）。
    """
    if not memory_ids:
        return 0
    now = now if now is not None else datetime.utcnow()

    memories = list_memories_to_reinforce(db, user_id, memory_ids)
    if not memories:
        return 0

    reinforced = 0
    for memory in memories:
        memory.access_count = (memory.access_count or 0) + 1
        memory.last_accessed_at = now

        if memory.status == DECAYING:
            memory.status = ACTIVE  # 降级拉回
        if memory.status in (ACTIVE, REINFORCED) and (
            memory.access_count >= config.REINFORCE_ACCESS_THRESHOLD
        ):
            memory.status = REINFORCED
        reinforced += 1

    db.commit()
    return reinforced


def run_decay(
    db: Session,
    user_id: int,
    *,
    now: datetime | None = None,
) -> dict:
    """一次生命周期清扫，返回各状态迁移的计数。

    三段互不重叠：
      1. 过期直归档：expires_at 已到 → ARCHIVED（不管现在什么状态）
      2. 冷落降级：低 importance + 长期未访问 → DECAYING
      3. 观察窗到期：DECAYING 里 updated_at 已过观察窗 → ARCHIVED

    顺序上有讲究：先处理"明确该归档的"，再让剩下的进入 DECAYING，
    避免一条刚被降级的记忆在同一轮里又被归档（它至少还有 30 天观察窗）。
    """
    now = now if now is not None else datetime.utcnow()
    expired_before = now - timedelta(days=config.DECAY_OBSERVATION_DAYS)
    stale_before = now - timedelta(days=config.DECAY_NO_ACCESS_DAYS)

    expired_count = 0
    stale_count = 0
    archived_count = 0

    # 1) 过期直归档
    for memory in list_expired_memories(db, user_id, now=now):
        if memory.status == ARCHIVED:  # 防御：防止同一轮重复归档
            continue
        memory.status = ARCHIVED
        memory.updated_at = now
        expired_count += 1

    # 2) 冷落降级：list_stale_memories 只挑 ACTIVE（REINFORCED 免于冷落
    #    降级），这里再守一道是因为 python 状态可能已被第 1 段改成
    #    ARCHIVED，避免把"该归档"的又拉去降级。
    for memory in list_stale_memories(
        db,
        user_id,
        older_than=stale_before,
        max_importance=config.DECAY_IMPORTANCE_THRESHOLD,
    ):
        if memory.status != ACTIVE:
            continue
        memory.status = DECAYING
        memory.updated_at = now  # 观察窗起点 = 进入 DECAYING 的时刻
        stale_count += 1

    # 3) 观察窗到期的 DECAYING 归档
    for memory in list_decaying_memories(
        db, user_id, older_than=expired_before
    ):
        if memory.status != DECAYING:
            continue
        memory.status = ARCHIVED
        memory.updated_at = now
        archived_count += 1

    db.commit()
    logger.info(
        "用户 %s 生命周期清扫完成: 过期归档 %s, 降级 %s, 观察到期归档 %s",
        user_id,
        expired_count,
        stale_count,
        archived_count,
    )
    return {
        "expired_archived": expired_count,
        "decayed": stale_count,
        "observation_archived": archived_count,
    }
