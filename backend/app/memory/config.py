"""记忆排序/召回的可调参数 —— 唯一权威位置。

Phase 4 起，所有"调多少分"的旋钮集中在本模块，检索和排序代码只从这里读，
不再各自引用 Settings / 在函数里拼数字。改参数不动逻辑，也不动别的模块。

三组旋钮：
1. FACTORS + TYPE_WEIGHTS —— 五因子加权，semantic / episodic 各一套权重，
   每套之和为 1.0。想让"exact key 命中"更硬就调 semantic.exactness；
   想让 episodic 更看重新鲜度就调 episodic.recency。
2. 时效性衰减 —— 半衰期 + 衰减函数名。新增衰减函数注册进
   RECENCY_DECAY_FUNCTIONS，配置里指名字即可切换，不用改调用方。
3. FALLBACK_SIMILARITY —— 非向量路（字面 / 会话）命中时给的相似度回退值，
   表示"这条命中有多像一次真正的语义命中"。
"""

from typing import Callable

from ..core.config import settings

# ---------------------------------------------------------------------------
# 五因子
# ---------------------------------------------------------------------------

# 排序因子（顺序即 breakdown 输出顺序，保持稳定）：
#   similarity 语义相似度：向量命中取真值；字面/会话命中取 FALLBACK 回退值
#   importance 重要性：提取时 LLM 打分（用户的核心目标、长期偏好）
#   recency    时效性：距最近一次访问的天数，按 RECENCY_DECAY 衰减
#   confidence 置信度：提取时 LLM 给的可信度
#   exactness  精确命中：semantic 的 key 被完整命中取 1.0，其余取 0.0；
#              episodic 没有 key，恒为 0，对应权重也应配 0
FACTORS: tuple[str, ...] = (
    "similarity",
    "importance",
    "recency",
    "confidence",
    "exactness",
)

# 每种记忆类型各一套权重，各自之和为 1.0。为什么分两套：
#   semantic 是稳定事实 —— "用户在哪工作"存十年依然为真，时效性最不重要；
#   反过来它的检索入口常常就是用户原话（key 精确命中），exactness 必须有力，
#   否则一条模糊但语义接近的向量命中会把字面命中的正主挤下去。
#   episodic 是一次事件 —— 越近越相关，recency 权重最高；没有 key 可精确命中。
TYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "semantic": {
        "similarity": 0.30,
        "importance": 0.25,
        "recency": 0.10,
        "confidence": 0.15,
        "exactness": 0.20,
    },
    "episodic": {
        "similarity": 0.35,
        "importance": 0.30,
        "recency": 0.25,
        "confidence": 0.10,
        "exactness": 0.00,
    },
}

# ---------------------------------------------------------------------------
# 时效性衰减
# ---------------------------------------------------------------------------

# 指数衰减的半衰期（天）。来自 Settings，保留部署期环境变量覆盖的通道；
# 半衰期前的记忆 recency 记为 0.5。
RECENCY_HALF_LIFE_DAYS: float = settings.memory_recency_half_life_days

# 当前选用的衰减函数名，取值见 RECENCY_DECAY_FUNCTIONS。
RECENCY_DECAY: str = "exponential"


def _exp_decay(age_days: float, half_life_days: float) -> float:
    """指数衰减：半衰期处 0.5，两倍半衰期处 0.25。MVP 起沿用的默认。"""
    return 0.5 ** (age_days / half_life_days)


def _linear_decay(age_days: float, half_life_days: float) -> float:
    """线性衰减：两倍半衰期处降到 0（半衰期处正好 0.5）。"""
    return max(0.0, 1.0 - age_days / (2.0 * half_life_days))


def _flat_decay(age_days: float, half_life_days: float) -> float:
    """不衰减：永远 1.0（需要关掉时效性因子时用）。"""
    return 1.0


RECENCY_DECAY_FUNCTIONS: dict[str, Callable[[float, float], float]] = {
    "exponential": _exp_decay,
    "linear": _linear_decay,
    "flat": _flat_decay,
}


# ---------------------------------------------------------------------------
# 非向量路的相似度回退值
# ---------------------------------------------------------------------------

# 向量能算真实相似度；另外三路拿不到，就按"命中强度"给固定值当作
# similarity 因子的输入：key 被完整命中 > 正文命中关键词 > 只是同一会话。
FALLBACK_SIMILARITY: dict[str, float] = {
    "exact_key": 1.0,
    "keyword": 0.6,
    "session": 0.3,
}


def weights_for(type_: str) -> dict[str, float]:
    """取某类型记忆的权重表副本。返回副本，避免调用方误改全局配置。"""
    if type_ not in TYPE_WEIGHTS:
        raise ValueError(
            f"未知记忆类型: {type_!r}（可用: {sorted(TYPE_WEIGHTS)}）"
        )
    return dict(TYPE_WEIGHTS[type_])


# ---------------------------------------------------------------------------
# 记忆合并（Consolidation，Phase 5）
# ---------------------------------------------------------------------------

# 只在这个时间窗内找重复事件：太久远的要么已合并过，要么不值得再花
# 一次 LLM 调用去归纳。
CONSOLIDATION_WINDOW_DAYS: int = 7
# 少于这么多条的组不合并 —— 单条事件没有"重复"可言。
CONSOLIDATION_MIN_GROUP_SIZE: int = 2
# 两条 episodic 摘要向量的余弦相似度达到这个值，才认为在讲同一类事。
CONSOLIDATION_SIMILARITY_THRESHOLD: float = 0.78
# 一次手动合并最多处理几组：每组一次 LLM 调用，限制成本与延迟。
CONSOLIDATION_MAX_GROUPS: int = 5


# ---------------------------------------------------------------------------
# 生命周期（Decay / Reinforcement / Archive，Phase 6）
# ---------------------------------------------------------------------------

# 一条 ACTIVE/REINFORCED 记忆连续这么多天没被访问、且 importance 低于
# 阈值，进入退化观察（DECAYING）。
DECAY_NO_ACCESS_DAYS: int = 90
# importance 低于该值的 ACTIVE 记忆才可能被标记退化 —— 高重要性记忆
# （用户核心目标）即使长期没用到也不该自动降级。
DECAY_IMPORTANCE_THRESHOLD: float = 0.5
# DECAYING 观察窗（天）：进入退化后再等这么久仍无人访问 → 归档。
DECAY_OBSERVATION_DAYS: int = 30

# 每次 reinforce（确认被真正用上）：
#   - access_count +1，last_accessed_at 刷新
#   - confidence 上调一步（语义：用到了 → 更信这条没记错）
REINFORCE_CONFIDENCE_STEP: float = 0.05
# access_count 累计达到该值 → 状态提升为 REINFORCED
REINFORCE_ACCESS_THRESHOLD: int = 3
