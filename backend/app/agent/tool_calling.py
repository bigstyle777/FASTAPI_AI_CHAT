"""
Tool Calling 主循环（异步）

职责: 把模型返回的"工具调用意图"翻译成真实执行，再把执行结果
以 role="tool" 的消息回传给模型，直到模型给出最终回答。

本文件不关心具体有哪些工具——工具列表来自 app/tools 的汇总，
新增工具时只需要在 tools/ 目录加模块，这里不用改。

关于 async
----------
整条链路是 async 的：``await client.chat.completions.create(...)``，
等待大模型回包的几十秒里事件循环是空的，可以同时处理别的请求。

工具本身有两种，主循环对它们一视同仁：

- MCP 工具（``MCPTool``）：``__call__`` 是 async 的，直接 await。
  它内部会把调用交给 MCP 后台事件循环再接回来，等待期间同样不占线程；
- 本地工具（calculator / rag_search / web_search ...）：同步函数，
  内部会查库或发 HTTP，统一丢线程池，避免阻塞事件循环。

一轮里模型可能一次要调多个工具，它们之间没有依赖，用 ``asyncio.gather``
并行执行（``gather`` 保证结果的顺序与传入顺序一致）。
"""

import asyncio
import inspect
import json
import logging
from time import perf_counter
from typing import Any, AsyncGenerator, Callable

from starlette.concurrency import run_in_threadpool

from ..chat.schemas import StreamDeltaEvent, StreamUsageEvent, TokenUsage
from ..tools import ALL_TOOLS, TOOL_REGISTRY

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 5


def _is_async_tool(func: Callable[..., Any]) -> bool:
    """调用这个工具会返回协程吗？

    ``MCPTool`` 这种"拿实例当函数用"的对象，async 的是它的 ``__call__``，
    而 ``inspect.iscoroutinefunction(实例)`` 会返回 False，所以要往下看一层。

    必须在真正调用**之前**判断：同步工具一旦调用就已经把线程占住了，
    事后再检查返回值来不来得及都没用。
    """
    if inspect.iscoroutinefunction(func):
        return True
    call = getattr(func, "__call__", None)
    return call is not None and inspect.iscoroutinefunction(call)


async def _call_tool(func: Callable[..., Any], args: dict, context: dict | None):
    """执行工具函数；函数声明了 db / user_id 形参时自动注入会话上下文。"""
    if context:
        params = inspect.signature(func).parameters
        inject = {
            name: context[name]
            for name in ("db", "user_id")
            if name in params and name in context
        }
        args = {**args, **inject}

    if _is_async_tool(func):
        # MCP 工具：直接 await，不需要任何同步桥接
        return await func(**args)

    # 同步工具：丢线程池，别占着事件循环
    return await run_in_threadpool(func, **args)


async def _tool_error(
    call_id: str,
    name: str,
    error_msg: str,
    error_type: str,
    *,
    on_tool_result: Callable[..., Any] | None = None,
    duration_ms: int = 0,
    **extra,
) -> str:
    """工具调用失败的统一出口：记一次 trace，再返回给模型看的错误 JSON。

    "工具不存在 / 参数不是合法 JSON / 执行抛异常"这三种失败，除了错误信息
    和想额外带回去的字段不同，处理流程完全一样，所以收在这里。
    模型拿到错误 JSON 后可以自行决定修正参数重试。
    """
    if on_tool_result:
        await on_tool_result(
            call_id,
            name,
            None,
            error=error_msg,
            error_type=error_type,
            duration_ms=duration_ms,
        )
    return json.dumps(
        {"error": error_msg, "error_type": error_type, **extra},
        ensure_ascii=False,
    )


async def execute_tool_call(
    tool_call,
    context: dict | None = None,
    *,
    on_tool_call: Callable[..., Any] | None = None,
    on_tool_result: Callable[..., Any] | None = None,
) -> str:
    """执行单个工具调用，返回给模型看的 JSON 字符串结果。

    通过 on_tool_call / on_tool_result 回调向外暴露 trace 点，两个回调都是
    异步的（trace 落库是同步 DB 操作，要走线程池），所以这里统一 await：

    - await on_tool_call(tool_call_id, tool_name, arguments)
    - await on_tool_result(tool_call_id, tool_name, result, *,
                           error, error_type, duration_ms)

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
        return await _tool_error(
            call_id,
            name,
            f"工具不存在: {name}",
            "ToolNotFound",
            on_tool_result=on_tool_result,
            available_tools=sorted(TOOL_REGISTRY),
        )

    try:
        args = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError as error:
        # 参数是模型生成的，告诉它 JSON 不合法，让它自己修正后重试
        logger.warning("工具 %s 的参数不是合法 JSON: %s", name, error)
        return await _tool_error(
            call_id,
            name,
            f"参数不是合法 JSON: {error}",
            "InvalidArguments",
            on_tool_result=on_tool_result,
            raw_arguments=raw_arguments,
        )

    if on_tool_call:
        await on_tool_call(call_id, name, args)

    started_at = perf_counter()
    try:
        result = await _call_tool(func, args, context)
        logger.info("工具调用成功: %s", name)
        if on_tool_result:
            await on_tool_result(
                call_id,
                name,
                result,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
        return json.dumps(result, ensure_ascii=False)
    except Exception as error:  # noqa: BLE001
        # 把异常变成 JSON 回传给模型，模型能理解错误并决定是否修正参数重试
        logger.warning("工具 %s 执行失败: %s", name, error)
        return await _tool_error(
            call_id,
            name,
            str(error),
            type(error).__name__,
            on_tool_result=on_tool_result,
            duration_ms=int((perf_counter() - started_at) * 1000),
            tool=name,
        )


async def _run_tool_calls(tool_calls, context, on_tool_call, on_tool_result) -> list[str]:
    """并行执行一轮里的多个工具调用，返回与 tool_calls 同序的结果列表。

    模型一次给出多个调用时（比如同时查天气和搜索网页），这些调用之间通常
    没有依赖，串行跑会让总耗时变成所有工具之和；用 gather 并行后只等于
    最慢的那个。gather 保证结果顺序跟传入顺序一致，可以直接 zip 回
    tool_call_id。
    """
    return await asyncio.gather(
        *[
            execute_tool_call(
                tool_call,
                context,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
            )
            for tool_call in tool_calls
        ]
    )


async def run_tool_loop(
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
        response = await client.chat.completions.create(
            model=model,
            messages=history,
            tools=ALL_TOOLS,
        )
        message = response.choices[0].message

        # 模型不再调用工具，直接给出最终回答
        if not message.tool_calls:
            return history, message.content or ""

        # 模型的调用请求必须原样加入历史
        history.append(message)
        contents = await _run_tool_calls(
            message.tool_calls, context, on_tool_call, on_tool_result
        )
        history.extend(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": content,
            }
            for tool_call, content in zip(message.tool_calls, contents)
        )

    return history, None


class _StreamRound:
    """一轮流式响应的聚合结果：分片 tool_calls / 完整正文 / usage。"""

    def __init__(self):
        self.tool_call_parts: dict[int, dict] = {}
        self.content = ""
        self.usage = None


def _merge_tool_call_fragment(parts: dict[int, dict], fragments) -> None:
    """把流式返回的 tool_calls 分片按 index 聚合进 parts（参数增量拼接）。"""
    for tc in fragments:
        index = getattr(tc, "index", 0)
        entry = parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if getattr(tc, "id", None):
            entry["id"] = tc.id
        function = getattr(tc, "function", None)
        if function:
            if getattr(function, "name", None):
                entry["name"] = function.name
            if getattr(function, "arguments", None):
                entry["arguments"] += function.arguments


def _assembled_tool_calls(parts: dict[int, dict]) -> list[dict]:
    """把聚合好的分片还原成完整 tool_calls 列表（按 index 排序）。"""
    return [
        {
            "id": parts[index]["id"],
            "type": "function",
            "function": {
                "name": parts[index]["name"],
                "arguments": parts[index]["arguments"],
            },
        }
        for index in sorted(parts)
    ]


async def _consume_stream_round(
    response, round_result: _StreamRound
) -> AsyncGenerator[StreamDeltaEvent, None]:
    """消费一轮流式响应：逐段 yield 正文增量，聚合结果写进 round_result。

    聚合结果不用 return 带出来，是因为异步生成器的返回值没法像同步
    ``yield from`` 那样直接拿到，传个容器进来更简单直白。
    """
    async for chunk in response:
        if getattr(chunk, "usage", None):
            round_result.usage = chunk.usage
            continue

        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        if delta is None:
            continue

        if getattr(delta, "tool_calls", None):
            _merge_tool_call_fragment(round_result.tool_call_parts, delta.tool_calls)
            continue

        content = getattr(delta, "content", None)
        if content:
            round_result.content += content
            yield StreamDeltaEvent(content=content)


async def stream_with_tools(
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

        response = await client.chat.completions.create(**kwargs)

        round_result = _StreamRound()
        async for event in _consume_stream_round(response, round_result):
            yield event

        # 这一轮流里有工具调用请求，执行后带着结果继续下一轮
        if round_result.tool_call_parts and use_tools:
            tool_calls = _assembled_tool_calls(round_result.tool_call_parts)

            # trace 起点：把每个调用暴露出去，
            # agent 层据此记录"模型发起了工具调用"这一节点
            if on_tool_call:
                for tc in tool_calls:
                    arguments = tc["function"]["arguments"]
                    try:
                        parsed_args = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError:
                        parsed_args = {"raw": arguments}
                    await on_tool_call(tc["id"], tc["function"]["name"], parsed_args)

            history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                }
            )
            contents = await _run_tool_calls(
                tool_calls, context, None, on_tool_result
            )
            history.extend(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": content,
                }
                for tool_call, content in zip(tool_calls, contents)
            )
            continue

        # 正常回答结束，输出 token 用量
        if round_result.usage:
            yield StreamUsageEvent(
                usage=TokenUsage(
                    prompt_tokens=round_result.usage.prompt_tokens,
                    completion_tokens=round_result.usage.completion_tokens,
                    total_tokens=round_result.usage.total_tokens,
                    model=model,
                )
            )
        return
