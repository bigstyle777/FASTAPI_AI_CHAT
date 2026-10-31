"""Agent 运行内的 usage 累加。

Agent 一次运行会调用多次模型（规划、每步执行、工具轮次、总结），
每次调用都有自己的 usage。这里把它们累加到一个 TokenUsage 上，
收尾时统一写 agent_runs 和 llm_usage_logs。
"""

from ..chat.schemas import TokenUsage


def accumulate_usage(
    total: TokenUsage,
    usage,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> TokenUsage:
    """把一个上游 usage 累加进 total；usage 为空时只更新模型标识。"""
    if total is None:  # 兼容不关心记账的裸调用（测试 / 旧路径）
        return usage
    if usage is not None:
        total.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        total.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        total.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
    if model:
        total.model = model
    if provider:
        total.provider = provider
    return total


def snapshot_usage(total: TokenUsage) -> TokenUsage:
    """给事件一个副本，避免下游拿到还在变化的同一个对象。"""
    return TokenUsage(**total.model_dump())
