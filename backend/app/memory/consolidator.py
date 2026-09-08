"""Memory 合并（Consolidation，Phase 5）：把重复的 episodic 聚成高层总结。

解决的问题
----------
episodic 记录"发生过的事"，同一类事件会在一段时间里反复落库 —— 用户
一周里三次表达"讨厌魔法数字"，就存三条几乎同义的 episodic。单看每条
都够格入库，但它们撑大候选池、互相稀释重要性。用户真正关心的是
"他一直在抱怨魔法数字"这一条更高层的结论。

做法
----
时间窗内把语义相似的 episodic 聚类成组（摘要向量余弦相似度），
每组调用一次 LLM：把"这次这样、那次那样"归纳成一条更抽象的 episodic。
原 episodes 不删除，标 status=CONSOLIDATED 并指向新总结 ——
metadata.source_episode_ids 永远能溯源回合并前原文。

边界
----
- 只合并 episodic。semantic 按 (user_id, key) upsert，天然只有一条，
  没有"重复堆叠"的问题，不需要合并。
- 不注册任何定时器：按用户决定由 Celery 手动任务触发（task/memory_*），
  需要定期跑将来在调度层加，不进本模块。
- 单组 LLM 调用失败只跳过该组，不影响其它组。
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..core.ai_client import get_client, get_user_ai_settings
from ..core.config import settings
from ..rag.embedding import embed_texts
from . import config
from .repository import consolidate_episodes, list_consolidation_candidates
from .status import CONSOLIDATED

logger = logging.getLogger(__name__)

CONSOLIDATION_SYSTEM_PROMPT = """\
你负责把一组"同一类事件的重复记录"归纳成一条更高层的总结。

输入是一段时间内用户反复表达的相似事件（每条带时间与类型）。
请输出一条更高层的 episodic 总结：
- summary：不再复述每一次的细节，而是概括这个重复模式本身
  （"用户最近多次抱怨…"、"过去一周用户两次提到…"这类写法），
  让将来的检索一看就知道这是一条长期观察，而不是某一次具体事件；
- importance：这条长期观察对以后有多大用（0~1）；
- confidence：你有把握归纳得不错的分值（0~1）；
- event_type：用一个词概括，如 preference / goal / decision。

只返回 JSON，不要任何解释文字或代码块标记，格式：
{"summary": "...", "importance": 0.8, "confidence": 0.9, "event_type": "preference"}
"""


@dataclass
class ConsolidationResult:
    """一次"尝试合并一组"的结果。

    - memory 不为 None 表示合并成功（新建的高层总结）；
    - memory 为 None 时 reason 说明为什么没成，供任务日志与测试断言。
    """

    memory: object | None
    reason: str = ""


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """两个向量的余弦相似度。空向量按 0 处理，不抛异常。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def cluster_episodes(
    items: list[tuple[int, list[float]]],
    *,
    threshold: float,
) -> list[list[int]]:
    """把 (id, embedding) 列表按相似度贪心聚类，返回组内 id 列表。

    输入按时间升序，簇内用"第一个成员"作代表向量：新事件只跟每个
    组的代表比，达到阈值就近加入。O(n*k) 足够小（k 是组数），
    不需要上层次聚类。返回的组不含低于 2 条的单例（单条不算重复）。

    纯函数、不碰数据库 —— 方便用假向量做单测。
    """
    groups: list[tuple[list[int], list[float]]] = []  # (ids, 代表向量)
    for item_id, vector in items:
        best_index = -1
        best_similarity = threshold  # 必须 >= 阈值才算命中
        for index, (_, representative) in enumerate(groups):
            similarity = cosine_similarity(vector, representative)
            if similarity > best_similarity:
                best_index = index
                best_similarity = similarity
        if best_index == -1:
            groups.append(([item_id], vector))
        else:
            groups[best_index][0].append(item_id)

    return [ids for ids, _ in groups if len(ids) >= 2]


def find_candidates(
    db: Session,
    user_id: int,
    *,
    window_days: int | None = None,
    threshold: float | None = None,
    max_groups: int | None = None,
) -> list[list[object]]:
    """找出当前用户可合并的 episodic 分组（每组 >= 2 条，按最新组在前）。

    window_days / threshold / max_groups 默认取 config 里的值，
    测试可以临时传小窗口或高阈值。
    """
    window_days = window_days or config.CONSOLIDATION_WINDOW_DAYS
    threshold = (
        config.CONSOLIDATION_SIMILARITY_THRESHOLD
        if threshold is None
        else threshold
    )
    max_groups = max_groups or config.CONSOLIDATION_MAX_GROUPS

    since = datetime.utcnow() - timedelta(days=window_days)
    rows = list_consolidation_candidates(db, user_id, since=since)
    items = [(m.id, m.embedding) for m in rows if m.embedding is not None]

    groups = cluster_episodes(items, threshold=threshold)

    # 组内按时间升序（repository 已保证），组间按"最近一条"降序
    rows_by_id = {m.id: m for m in rows}
    result: list[list[object]] = []
    for ids in groups:
        members = [rows_by_id[i] for i in ids]
        result.append(members)
    result.sort(key=lambda group: max(m.created_at for m in group), reverse=True)
    return result[:max_groups]


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -3]
    return stripped.strip()


def _clamp01(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def _parse_summary(raw: str) -> dict:
    """解析 LLM 返回的合并 JSON，解析失败抛 ValueError 交给上层决定。"""
    candidate = _strip_code_fence(raw or "")
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"找不到 JSON 对象: {raw[:200]!r}")
    data = json.loads(candidate[start : end + 1])
    summary = str(data.get("summary") or "").strip()
    if not summary:
        raise ValueError("summary 为空")
    return {
        "summary": summary,
        "importance": _clamp01(data.get("importance")),
        "confidence": _clamp01(data.get("confidence")),
        "event_type": str(data.get("event_type") or "insight").strip()[:40],
    }


def _describe_group(memories) -> str:
    """把一组 episodic 压成给 LLM 的文本，按时间升序，带日期与类型。"""
    lines = []
    for index, memory in enumerate(memories, start=1):
        date = memory.created_at.strftime("%Y-%m-%d") if memory.created_at else "?"
        tag = memory.event_type or "episode"
        lines.append(f"{index}) [{tag}] {date}: {memory.summary}")
    return "\n".join(lines)


def consolidate_group(
    db: Session,
    user_id: int,
    memories,
    *,
    client,
    model: str,
) -> ConsolidationResult:
    """合并一组记忆：调 LLM 归纳 → 原子落库（新总结 + 原文标记）。

    任何一步失败都返回 reason 而不抛异常 —— 合并是后台任务，一组失败
    不该让整个任务重试。LLM 不可用、解析失败、组已失效各有明确原因。
    """
    if len(memories) < 2:
        return ConsolidationResult(None, "too_few")
    if any(
        getattr(m, "status", "active") == CONSOLIDATED for m in memories
    ):
        return ConsolidationResult(None, "already_consolidated")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "以下是同一类事件的重复记录，请归纳成一条高层总结：\n\n"
                        + _describe_group(memories)
                    ),
                },
            ],
            max_tokens=500,
        )
        content = response.choices[0].message.content or ""
        parsed = _parse_summary(content)
    except Exception:
        logger.warning("合并 LLM 调用/解析失败，跳过该组", exc_info=True)
        return ConsolidationResult(None, "llm_failed")

    embedding = None
    if settings.memory_embedding_enabled:
        try:
            # allow_fallback=False：宁可让总结先以无向量落库（下轮回填），
            # 也不要存一个哈希伪向量 —— 它不为 NULL，回填永远不会再看它
            embedding = embed_texts(
                [parsed["summary"]], user_id, db, allow_fallback=False
            )[0]
        except Exception:
            logger.warning("合并总结向量化不可用，暂以无向量落库", exc_info=True)

    merged = consolidate_episodes(
        db,
        user_id,
        [m.id for m in memories],
        summary=parsed["summary"],
        importance=parsed["importance"],
        confidence=parsed["confidence"],
        event_type=parsed["event_type"],
        embedding=embedding,
        metadata={
            "merged_on": datetime.utcnow().isoformat(),
            "source_episode_ids": [m.id for m in memories],
        },
    )
    if merged is None:
        return ConsolidationResult(None, "source_no_longer_active")
    return ConsolidationResult(merged, "")


def run_consolidation_for_user(db: Session, user_id: int) -> dict:
    """当前用户的一次完整合并流程（Celery 手动任务入口）。

    流程：按用户 AI 配置建 client → find_candidates 找组 →
    逐组 consolidate_group。返回统计 dict，失败不抛异常。
    """
    api_key, provider = get_user_ai_settings(user_id=user_id, db=db)
    result = get_client(api_key=api_key, provider=provider)
    if not result:
        logger.info("用户 %s 没有可用的 AI 配置，跳过记忆合并", user_id)
        return {"consolidated": 0, "reason": "no_ai_config"}

    client, model = result
    groups = find_candidates(db, user_id)
    consolidated_ids: list[int] = []
    for group in groups:
        outcome = consolidate_group(db, user_id, group, client=client, model=model)
        if outcome.memory is not None:
            consolidated_ids.append(outcome.memory.id)

    return {
        "consolidated": len(consolidated_ids),
        "memory_ids": consolidated_ids,
        "groups_checked": len(groups),
    }
