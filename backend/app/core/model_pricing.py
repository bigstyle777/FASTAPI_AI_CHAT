"""模型单价表与成本折算。

金额统一用"微元"（1e-6 元）存整数：一次调用可能只有几分钱，用浮点
累加到月底会跑偏；整数微元既能精确对账，也方便存 PG 的 BIGINT/INT。

价格表放代码而非 DB：改价跟着发布走、可 review，避免"运行中改价导致
历史账目口径不一致"。查找按模型名前缀**最长匹配**，所以
``deepseek-v4-flash`` 能命中 ``deepseek-`` 规则，``gpt-4o-mini`` 能命中
自己的规则而不是 ``gpt-4o``。

未知模型返回 None，成本记 0 —— 缺价目表绝不能导致丢账或调用失败。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    provider: str
    # 每 1K 输入 token 的价格（微元）
    input_micros_per_1k: int
    # 每 1K 输出 token 的价格（微元）
    output_micros_per_1k: int


# 单位为"元/1K tokens"换算成微元。价格是部署期静态值，改价随代码发布。
_PRICE_RULES: tuple[tuple[str, ModelPrice], ...] = (
    ("deepseek-reasoner", ModelPrice("deepseek", 4000, 16000)),
    ("deepseek-chat", ModelPrice("deepseek", 2000, 8000)),
    ("deepseek-v4", ModelPrice("deepseek", 2000, 8000)),
    ("deepseek", ModelPrice("deepseek", 2000, 8000)),
    ("gpt-4o-mini", ModelPrice("openai", 1100, 4300)),
    ("gpt-4o", ModelPrice("openai", 18000, 72000)),
    ("gpt-3.5", ModelPrice("openai", 500, 1500)),
)


def price_for(model: str | None) -> ModelPrice | None:
    """按模型名做前缀最长匹配；没有对应价目时返回 None。"""
    if not model:
        return None

    name = model.lower()
    matched: tuple[str, ModelPrice] | None = None
    for prefix, price in _PRICE_RULES:
        if name.startswith(prefix) and (matched is None or len(prefix) > len(matched[0])):
            matched = (prefix, price)
    return matched[1] if matched else None


def cost_micros(
    provider: str | None,
    model: str | None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> int:
    """折算一次调用的成本（微元）；未知模型返回 0。

    ``provider`` 仅用于调用方校验/记录，单价按模型名匹配，因为同一家
    供应商的不同模型价格差别远大于跨供应商的命名差异。
    """
    price = price_for(model)
    if price is None:
        return 0

    return (
        prompt_tokens * price.input_micros_per_1k
        + completion_tokens * price.output_micros_per_1k
    ) // 1000
