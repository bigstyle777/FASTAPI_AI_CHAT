"""
Tool Calling 学习示例（完整流程）
====================================

运行方式（在 backend 目录下）:

    python -m examples.tool_calling_demo
    python -m examples.tool_calling_demo "帮我算一下 (15 + 7) * 2"

它会读取项目根目录 .env 里的 API Key 和模型配置。
默认走 DeepSeek（和项目 llm.py 一致），也可以改成 OpenAI。

Tool Calling 一共就三件事:
    1. 用 JSON Schema 告诉模型"你有这些工具可以用"
    2. 模型返回"我想调用 calculator, 参数是 {...}"（只是意图，不真正执行）
    3. 你在自己代码里真正执行这个函数，再把结果作为 tool 消息回传给模型，
       让模型基于结果生成最终回答

本文件把三步都放在一起，方便你对照学习。
"""

import json
import sys

from app.core.config import settings
from openai import OpenAI

# ---------------------------------------------------------------------------
# 第 1 步: 定义工具的 JSON Schema
# 模型不会执行你的函数，它只根据这个 schema 决定"调哪个工具、传什么参数"。
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行基本数学运算，例如加减乘除",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "第一个数字"},
                    "b": {"type": "number", "description": "第二个数字"},
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "要执行的运算",
                    },
                },
                "required": ["a", "b", "operation"],
                "additionalProperties": False,
            },
        },
    }
]


# ---------------------------------------------------------------------------
# 第 2 步: 实现工具的真实逻辑
# 名字和 TOOLS 里的 "name" 一一对应，运行时代码从这里真正执行。
# ---------------------------------------------------------------------------
def calculator(a: float, b: float, operation: str) -> float:
    if operation == "add":
        return a + b
    if operation == "subtract":
        return a - b
    if operation == "multiply":
        return a * b
    if operation == "divide":
        if b == 0:
            raise ValueError("不能除以 0")
        return a / b
    raise ValueError(f"不支持的运算: {operation}")


# 工具注册表: name -> 函数，方便按模型返回的名字查找
TOOL_REGISTRY = {
    "calculator": calculator,
}


# ---------------------------------------------------------------------------
# 第 3 步: 工具调用主循环
# 流程: 发消息 -> 模型可能要调工具 -> 执行 -> 结果回传 -> 模型给最终回答
# 关键: 要写成 while 循环，因为模型可能连续调用多个工具。
# ---------------------------------------------------------------------------
def run_tool_calling_loop(user_input: str) -> str:
    # 和项目 llm.py 保持一致: 默认 DeepSeek, 换成 "openai" 则走 OpenAI
    provider = "deepseek"
    if provider == "openai":
        api_key = settings.openai_api_key
        base_url = settings.openai_base_url
        model = settings.openai_model
    else:
        api_key = settings.deepseek_api_key
        base_url = settings.deepseek_base_url
        model = settings.deepseek_model

    if not api_key:
        return "未配置 API Key，请在项目根目录 .env 中填写 DEEPSEEK_API_KEY 或 OPENAI_API_KEY"

    client = OpenAI(api_key=api_key, base_url=base_url)

    # 对话历史: 工具调用结果必须带着历史上下文继续，所以所有消息都追加进来
    messages = [{"role": "user", "content": user_input}]

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,  # ty: ignore[invalid-argument-type]
            tools=TOOLS,
        )
        message = response.choices[0].message

        # 情况一: 模型想调用工具
        if message.tool_calls:
            # 先把模型的"调用请求"加入历史，格式必须是 message 对象本身
            messages.append(message)

            for tool_call in message.tool_calls:
                print(
                    f"\n[模型想调用] {tool_call.function.name}"
                    f"({tool_call.function.arguments})"
                )

                # 解析参数 -> 查注册表 -> 真正执行
                args = json.loads(tool_call.function.arguments)
                func = TOOL_REGISTRY[tool_call.function.name]
                result = func(**args)
                print(f"[工具执行结果] {result}")

                # 把结果作为 role="tool" 的消息回传，tool_call_id 必须对应
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            # 带着工具结果继续问模型，让它给出最终回答
            continue

        # 情况二: 模型不再调工具，直接给出最终回答
        return message.content or "(空回答)"


def main() -> None:
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = "帮我算一下 (15 + 7) * 2，再算 100 / 4"

    print(f"问题: {question}")
    answer = run_tool_calling_loop(question)
    print(f"\n最终回答: {answer}")


if __name__ == "__main__":
    main()
