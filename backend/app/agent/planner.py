"""规划器：把用户任务拆成 PlanStep 列表。"""

import json
import logging
import re
from typing import Any

from ..chat.schemas import TokenUsage
from ..core.config import settings
from ..core.llm_gateway import complete
from .prompts import PLANNER_SYSTEM_PROMPT
from .state import PlanStep
from .usage import accumulate_usage

logger = logging.getLogger(__name__)


def _render_tools(tools) -> str:
    """把工具 Schema 渲染成提示词里的文本，防止模型编造参数名。"""
    entries = []
    for tool in tools or []:
        if isinstance(tool, str):
            fn = {"name": tool, "description": "", "parameters": {}}
        else:
            fn = tool.get("function", {}) if isinstance(tool, dict) else {}
            fn = {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        entries.append(json.dumps(fn, ensure_ascii=False))
    return "\n".join(f"- {entry}" for entry in entries)


def _tool_names(tools) -> set[str]:
    """从 schema 列表里提取合法工具名；为空表示不做白名单限制。"""
    names: set[str] = set()
    for tool in tools or []:
        if isinstance(tool, str):
            names.add(tool)
            continue
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function", tool)
        name = fn.get("name") if isinstance(fn, dict) else None
        if name:
            names.add(str(name))
    return names


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


async def _call_planner(
    client,
    model: str,
    payload: list[dict],
    *,
    targets=None,
    usage_sink: TokenUsage | None = None,
    provider: str | None = None,
):
    """调用规划模型：json_object 不被支持时退化为普通调用。"""
    kwargs: dict[str, Any] = {"temperature": 0.2}

    async def call(with_format: bool):
        call_kwargs = dict(kwargs)
        if with_format:
            call_kwargs["response_format"] = {"type": "json_object"}
        if targets:
            result = await complete(targets, messages=payload, **call_kwargs)
            return result.response
        return await client.chat.completions.create(
            model=model, messages=payload, **call_kwargs
        )

    try:
        response = await call(True)
    except Exception:
        response = await call(False)

    accumulate_usage(
        usage_sink,
        getattr(response, "usage", None),
        model=model,
        provider=provider,
    )
    return response


async def create_plan(
    client,
    model: str,
    messages: list[dict],
    *,
    available_tools: list[str] | None = None,
    max_steps: int = 6,
    targets=None,
    usage_sink: TokenUsage | None = None,
    provider: str | None = None,
) -> list[PlanStep]:
    """调用 LLM 生成计划，并校验成 PlanStep 列表。

    解析失败会重试（默认最多 2 次）；工具名不在白名单的步骤降级为
    "让 LLM 自己完成"，不直接丢掉整个计划。
    """
    tool_schemas = _render_tools(available_tools)
    allowed_tools = _tool_names(available_tools)
    system_message = {
        "role": "system",
        "content": PLANNER_SYSTEM_PROMPT.format(
            tools=tool_schemas or "（当前没有可用工具，所有步骤 tool 留空）",
            max_steps=max_steps,
        ),
    }
    payload = [system_message, *messages]

    attempts = max(1, settings.agent_planner_max_attempts)
    data: dict[str, Any] | None = None
    for attempt in range(attempts):
        try:
            response = await _call_planner(
                client,
                model,
                payload,
                targets=targets,
                usage_sink=usage_sink,
                provider=provider,
            )
            data = _extract_json(response.choices[0].message.content or "{}")
            break
        except Exception as error:  # noqa: BLE001
            if attempt == attempts - 1:
                raise
            logger.warning("规划输出解析失败（第 %d 次），重试一次: %s", attempt + 1, error)

    raw_steps = (data or {}).get("steps") or []
    steps: list[PlanStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description", "")).strip()
        if not description:
            continue
        tool = raw.get("tool") or None
        if tool and allowed_tools and tool not in allowed_tools:
            logger.warning("规划器给出未知工具 %s，降级为普通步骤", tool)
            tool = None
        steps.append(
            PlanStep(
                description=description,
                tool=tool,
                args=raw.get("args") if isinstance(raw.get("args"), dict) else {},
                expected_output=raw.get("expected_output") or None,
            )
        )
    return steps[:max_steps]
