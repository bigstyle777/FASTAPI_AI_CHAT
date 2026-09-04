"""独立 Reranking 模块：多路召回之后、取 Top-K 之前的排序层。

Phase 4 把它从 retriever 里拆出来单独成模块，原因：
- 打分不再只是一个"三因子加权和"，而是五因子加权 + 按记忆类型换权重 +
  可配置的时效衰减。继续藏在 retrieve 的私有函数里，想单独测、想调参
  都得穿过整个检索流程。
- 打分要给出可解释的中间量（每个因子的原始值和加权贡献），排查
  "这条为什么排前面"不用再手推一遍公式。

本模块不碰数据库、不依赖 ORM：输入是 Candidate（一条召回命中 + 它
是怎么被命中的），输出是 ScoredMemory（总分 + 逐因子明细）。
"""

from dataclasses import dataclass
from datetime import datetime

from . import config


@dataclass(frozen=True)
class Candidate:
    """一条被召回的候选：记忆对象 + 相似度 + 命中来源。

    source 说明这条是怎么被找到的，reranker 靠它决定 exactness 因子的取值：
      - "vector"     向量检索命中（semantic 的 value / episodic 的 summary）
      - "exact_key"  semantic 的 key 被完整命中（字面匹配）
      - "keyword"    semantic 的 key/value 命中关键词（子串匹配）
      - "session"    与查询无关，只是同一会话里产生过
    """

    memory: object
    similarity: float
    source: str = "vector"


@dataclass
class ScoredMemory:
    """打分结果：总分 + 逐因子明细，供测试和排查使用。

    - weights：这条记忆所用类型的权重表（与 config.TYPE_WEIGHTS[type] 相同）
    - factors：每个因子的原始值（0~1），打分公式的输入
    - breakdown：每个因子的加权贡献，weights[f] * factors[f]，
      全部相加即 score
    """

    memory: object
    type: str
    weights: dict[str, float]
    factors: dict[str, float]
    breakdown: dict[str, float]

    @property
    def score(self) -> float:
        return sum(self.breakdown.values())


def _memory_type(memory) -> str:
    """从记忆对象身上取类型字符串。

    ORM Memory.type 是字符串；MemoryRead.type 是 MemoryType 枚举；
    测试里手搓的 fake 也可能直接给字符串。统一都转成 "semantic"/"episodic"。
    """
    raw = getattr(memory, "type", None)
    if raw is None:
        return "semantic"
    return raw.value if hasattr(raw, "value") else str(raw)


def recency_score(
    memory,
    now: datetime,
    *,
    half_life_days: float | None = None,
    decay: str | None = None,
) -> float:
    """时效性因子：距最近一次访问的天数，按选定的衰减函数算分。

    - 记忆没被访问过时退回创建时间；
    - 两个时间都没有（理论上不会发生）取中性值 0.5；
    - 默认走 config.RECENCY_DECAY 的指数衰减；测试可临时传别的。
    """
    half_life_days = (
        config.RECENCY_HALF_LIFE_DAYS
        if half_life_days is None
        else half_life_days
    )
    decay = config.RECENCY_DECAY if decay is None else decay
    decay_fn = config.RECENCY_DECAY_FUNCTIONS.get(decay)
    if decay_fn is None:
        raise ValueError(
            f"未知的时效衰减函数: {decay!r}（可用: "
            f"{sorted(config.RECENCY_DECAY_FUNCTIONS)}）"
        )

    anchor = memory.last_accessed_at or memory.created_at
    if anchor is None:
        return 0.5
    age_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    return decay_fn(age_days, half_life_days)


def score_candidate(
    candidate: Candidate,
    now: datetime | None = None,
    *,
    half_life_days: float | None = None,
    decay: str | None = None,
) -> ScoredMemory:
    """给一条候选打五因子加权分。

    只依赖 Candidate.memory 上的 importance / confidence / type 属性和
    时间戳字段，不感知 ORM —— 传"长得像 Memory 的对象"即可，便于单测。
    """
    memory = candidate.memory
    now = now if now is not None else datetime.utcnow()
    mtype = _memory_type(memory)
    weights = config.weights_for(mtype)

    factors = {
        "similarity": candidate.similarity,
        "importance": memory.importance,
        "recency": recency_score(
            memory, now, half_life_days=half_life_days, decay=decay
        ),
        "confidence": memory.confidence,
        "exactness": 1.0 if candidate.source == "exact_key" else 0.0,
    }
    breakdown = {factor: weights[factor] * factors[factor] for factor in config.FACTORS}
    return ScoredMemory(
        memory=memory,
        type=mtype,
        weights=weights,
        factors=factors,
        breakdown=breakdown,
    )


def rerank(
    candidates,
    *,
    now: datetime | None = None,
    top_k: int | None = None,
    half_life_days: float | None = None,
    decay: str | None = None,
) -> list[ScoredMemory]:
    """给一批候选统一打分并按总分降序，可选截断到 top_k。

    输入是 Candidate 的可迭代对象（retriever 把多路召回合并去重后的
    dict 值传进来即可）。打分是纯函数，无副作用。
    """
    now = now if now is not None else datetime.utcnow()
    scored = [
        score_candidate(
            candidate,
            now,
            half_life_days=half_life_days,
            decay=decay,
        )
        for candidate in candidates
    ]
    scored.sort(key=lambda item: item.score, reverse=True)
    if top_k is not None and top_k > 0:
        scored = scored[:top_k]
    return scored
