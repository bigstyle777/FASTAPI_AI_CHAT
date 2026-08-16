"""
Tool Calling 主循环

职责: 把模型返回的"工具调用意图"翻译成真实执行，再把执行结果
以 role="tool" 的消息回传给模型，直到模型给出最终回答。

本文件不关心具体有哪些工具——工具列表来自 app/tools 的汇总，
新增工具时只需要在 tools/ 目录加模块，这里不用改。
"""

import inspect
import json
import logging
from time import perf_counter
from typing import Any, Callable

from ..schemas import StreamDeltaEvent, StreamUsageEvent, TokenUsage
from ..tools import ALL_TOOLS, TOOL_REGISTRY

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 5


def _call_tool(func: Callable[..., Any], args: dict, context: dict | None):
    """执行工具函数；函数声明了 db / user_id 形参时自动注入会话上下文。"""
    if not context:
        return func(**args)

    params = inspect.signature(func).parameters
    inject = {
        name: context[name]
        for name in ("db", "user_id")
        if name in params and name in context
    }
    return func(**args, **inject)


def execute_tool_call(
    tool_call,
    context: dict | None = None,
    *,
    on_tool_call: Callable[..., Any] | None = None,
    on_tool_result: Callable[..., Any] | None = None,
) -> str:
    """执行单个工具调用，返回给模型看的 JSON 字符串结果。

    通过 on_tool_call / on_tool_result 回调向外暴露 trace 点：
    - on_tool_call(tool_call_id, tool_name, arguments)
    - on_tool_result(tool_call_id, tool_name, result, *, error, error_type, duration_ms)
    普通聊天不传回调即可，agent 层通过回调把过程记入 trace。
    """
    if isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
        name = function.get("name", "")
        raw_arguments = function.get("arguments", "") or ""
        call_id = tool_call.get("id", "")
    else:
        name = getattr(getattr(tool_call, "function", None), "name", "")
        raw_arguments = (
            getattr(getattr(tool_call, "function", None), "arguments", "") or ""
        )
        call_id = getattr(tool_call, "id", "")

    func: Callable[..., Any] | None = TOOL_REGISTRY.get(name)
    if func is None:
        logger.warning("工具不存在: %s", name)
        if on_tool_result:
            on_tool_result(
                call_id,
                name,
                None,
                error=f"工具不存在: {name}",
                error_type="ToolNotFound",
                duration_ms=0,
            )
        return json.dumps(
            {
                "error": f"工具不存在: {name}",
                "error_type": "ToolNotFound",
                "available_tools": sorted(TOOL_REGISTRY),
            },
            ensure_ascii=False,
        )

    try:
        args = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError as error:
        # 参数是模型生成的，告诉它 JSON 不合法，让它自己修正后重试
        logger.warning("工具 %s 的参数不是合法 JSON: %s", name, error)
        if on_tool_result:
            on_tool_result(
                call_id,
                name,
                None,
                error=f"参数不是合法 JSON: {error}",
                error_type="InvalidArguments",
                duration_ms=0,
            )
        return json.dumps(
            {
                "error": f"参数不是合法 JSON: {error}",
                "error_type": "InvalidArguments",
                "raw_arguments": raw_arguments,
            },
            ensure_ascii=False,
        )

    if on_tool_call:
        on_tool_call(call_id, name, args)

    started_at = perf_counter()
    try:
        result = _call_tool(func, args, context)
        logger.info("工具调用成功: %s", name)
        if on_tool_result:
            on_tool_result(
                call_id,
                name,
                result,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
        return json.dumps(result, ensure_ascii=False)
    except Exception as error:  # noqa: BLE001
        # 把异常变成 JSON 回传给模型，模型能理解错误并决定是否修正参数重试
        logger.warning("工具 %s 执行失败: %s", name, error)
        if on_tool_result:
            on_tool_result(
                call_id,
                name,
                None,
                error=str(error),
                error_type=type(error).__name__,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
        return json.dumps(
            {
                "error": str(error),
                "error_type": type(error).__name__,
                "tool": name,
            },
            ensure_ascii=False,
        )


def run_tool_loop(
    client,
    model,
    messages,
    context: dict | None = None,
    max_turns=MAX_TOOL_TURNS,
    on_tool_call: Callable[..., Any] | None = None,
    on_tool_result: Callable[..., Any] | None = None,
):
    """
    非流式工具调用循环（用于普通聊天接口）。

    返回 (history, final_content):
        - final_content: 模型给出的最终回答文本
        - 没有注册工具或超过最大轮数时 final_content 为 None，由调用方兜底
    """
    if not ALL_TOOLS:
        return list(messages), None

    history = [dict(m) for m in messages]
    for _ in range(max_turns):
        response = client.chat.completions.create(
            model=model,
            messages=history,
            tools=ALL_TOOLS,
        )
        message: Unknown = response.choices[0].message

        # 模型不再调用工具，直接给出最终回答
        if not message.tool_calls:
            return history, message.content or ""

        # 模型的调用请求必须原样加入历史
        history.append(message)
        for tool_call in message.tool_calls:
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": execute_tool_call(
                        tool_call,
                        context,
                        on_tool_call=on_tool_call,
                        on_tool_result=on_tool_result,
                    ),
                }
            )

    return history, None


def stream_with_tools(
    client,
    model,
    messages,
    context: dict | None = None,
    *,
    on_tool_call: Callable[..., Any] | None = None,
    on_tool_result: Callable[..., Any] | None = None,
):
    """
    流式对话生成器（支持工具调用），产出 StreamDeltaEvent / StreamUsageEvent。

    流程:
        1. 像普通对话一样流式输出;
        2. 如果流中出现了 tool_calls，先把分片参数拼完整，执行真实函数，
           把结果追加进历史，再发起一次流式请求生成最终回答。
    """
    history = [dict(m) for m in messages]

    # 有工具时最多执行 MAX_TOOL_TURNS 轮工具调用，之后强制走一轮不带
    # tools 的请求，让模型直接回答，避免轮次耗尽后用户收到空回复。
    rounds = (MAX_TOOL_TURNS + 1) if ALL_TOOLS else 1

    for round_index in range(rounds):
        use_tools = bool(ALL_TOOLS) and round_index < MAX_TOOL_TURNS

        kwargs = {
            "model": model,
            "messages": history,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if use_tools:
            kwargs["tools"] = ALL_TOOLS

        response: Unknown = client.chat.completions.create(**kwargs)

        # 流式返回的 tool_calls 是分片的，按 index 聚合成完整参数
        tool_call_parts: dict[int, dict] = {}
        final_usage = None

        for chunk in response:
            if getattr(chunk, "usage", None):
                final_usage = chunk.usage
                continue

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            if delta is None:
                continue

            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    index = getattr(tc, "index", 0)
                    parts = tool_call_parts.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    if getattr(tc, "id", None):
                        parts["id"] = tc.id
                    function = getattr(tc, "function", None)
                    if function:
                        if getattr(function, "name", None):
                            parts["name"] = function.name
                        if getattr(function, "arguments", None):
                            parts["arguments"] += function.arguments
                continue

            content = getattr(delta, "content", None)
            if content:
                yield StreamDeltaEvent(content=content)

        # 这一轮流里有工具调用请求，执行后带着结果继续下一轮
        if tool_call_parts and use_tools:
            tool_calls = [
                {
                    "id": parts["id"],
                    "type": "function",
                    "function": {
                        "name": parts["name"],
                        "arguments": parts["arguments"],
                    },
                }
                for parts in (
                    tool_call_parts[index] for index in sorted(tool_call_parts)
                )
            ]

            # trace 起点：tool_call_parts 在这里组装完成，把每个调用暴露出去，
            # agent 层据此记录"模型发起了工具调用"这一节点
            if on_tool_call:
                for tc in tool_calls:
                    arguments = tc["function"]["arguments"]
                    try:
                        parsed_args = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError:
                        parsed_args = {"raw": arguments}
                    on_tool_call(tc["id"], tc["function"]["name"], parsed_args)

            history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                }
            )
            for tool_call in tool_calls:
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": execute_tool_call(
                            tool_call,
                            context,
                            on_tool_result=on_tool_result,
                        ),
                    }
                )
            continue

        # 正常回答结束，输出 token 用量
        if final_usage:
            yield StreamUsageEvent(
                usage=TokenUsage(
                    prompt_tokens=final_usage.prompt_tokens,
                    completion_tokens=final_usage.completion_tokens,
                    total_tokens=final_usage.total_tokens,
                    model=model,
                )
            )
        return
