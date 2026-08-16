"""规划器：把用户任务拆成 PlanStep 列表。"""

import json
import re
from typing import Any

from .prompts import PLANNER_SYSTEM_PROMPT
from .state import PlanStep


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 返回内容里稳妥地取出 JSON 对象（容忍 ```json 围栏和前后废话）。"""
    content = text.strip()
    # 去掉 markdown 代码块围栏
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, re.DOTALL)
    if fenced:
        content = fenced.group(1).strip()

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("规划器返回内容里找不到 JSON 对象")

    return json.loads(content[start : end + 1])


def create_plan(
    client,
    model: str,
    messages: list[dict],
    *,
    available_tools: list[str] | None = None,
    max_steps: int = 6,
) -> list[PlanStep]:
    """调用 LLM 生成计划，并校验成 PlanStep 列表。"""
    tool_names = ", ".join(available_tools or [])
    system_message = {
        "role": "system",
        "content": PLANNER_SYSTEM_PROMPT.format(
            tools=tool_names or "（当前没有可用工具，所有步骤 tool 留空）",
            max_steps=max_steps,
        ),
    }

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [system_message, *messages],
        "temperature": 0.2,
    }

    # 部分兼容接口不支持 response_format，失败时去掉再重试一次
    try:
        kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
    except Exception:
        kwargs.pop("response_format", None)
        response = client.chat.completions.create(**kwargs)

    content = response.choices[0].message.content or "{}"
    data = _extract_json(content)
    raw_steps = data.get("steps") or []

    steps: list[PlanStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description", "")).strip()
        if not description:
            continue
        steps.append(
            PlanStep(
                description=description,
                tool=raw.get("tool") or None,
                args=raw.get("args") if isinstance(raw.get("args"), dict) else {},
                expected_output=raw.get("expected_output") or None,
            )
        )
    return steps
