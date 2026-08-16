"""
Agent 框架示例（规划 -> 执行 -> 总结）
=======================================

运行方式（在 backend 目录下）:

    python -m examples.agent_demo
    python -m examples.agent_demo "查询北京今天的天气，然后把温度乘以 2"

它会读取项目根目录 .env 里的 API Key 和模型配置，走 DeepSeek 或 OpenAI。

与 tool_calling_demo.py 的对比:
    - tool calling: 单轮循环，模型自己决定要不要用工具、直接出答案；
    - agent:        planner 先把任务拆成步骤，executor 逐步执行
                    （步骤声明了工具就直接调，否则让 LLM 带着工具循环做），
                    finalizer 最后汇总成最终答案。

本示例不落数据库，trace 直接打印到终端；接入项目后 trace 会写入
agent_trace_points 表，可随时通过 GET /agent/runs/{run_id} 查看。
"""

import sys

from app.agent.agent import run_agent
from app.agent.state import AgentState
from app.agent.trace import NullTracer
from app.core.config import settings
from openai import OpenAI


class PrintTracer(NullTracer):
    """把 trace 点打印到终端的 tracer，方便不连数据库时观察执行过程。"""

    def point(self, stage, name, **kwargs):
        status = kwargs.get("status", "completed")
        if status == "started":
            print(f"  [trace] {stage}.{name} 开始")
        else:
            duration = kwargs.get("duration_ms")
            suffix = f" ({duration}ms)" if duration is not None else ""
            print(f"  [trace] {stage}.{name} {status}{suffix}")
        return None

    def emit(self, event):
        if getattr(event, "type", "") == "agent_tool":
            status = event.status
            detail = (
                f"结果={event.result}" if status == "completed" else f"错误={event.error}"
            )
            print(f"  [tool] {event.tool} {status}: {detail}")


def main() -> None:
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = "帮我算一下 (15 + 7) * 2，再算 100 / 4"

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
        print("未配置 API Key，请在项目根目录 .env 中填写 DEEPSEEK_API_KEY 或 OPENAI_API_KEY")
        return

    client = OpenAI(api_key=api_key, base_url=base_url)
    tracer = PrintTracer(run_id=0)

    print(f"问题: {question}\n")
    state: AgentState = run_agent(
        client,
        model,
        question,
        messages=[{"role": "user", "content": question}],
        tracer=tracer,
    )

    print("\n===== 计划 =====")
    for index, step in enumerate(state.plan, 1):
        tool = f" -> {step.tool}" if step.tool else ""
        print(f"{index}. {step.description}{tool}")

    print("\n===== 执行结果 =====")
    for result in state.results:
        status = result.status
        detail = (result.output or result.error or "").strip()
        print(f"{result.index + 1}. [{status}] {detail[:120]}")

    print(f"\n===== 最终回答 =====\n{state.final_answer}")
    print(f"\n状态: {state.status} | tokens: {state.total_tokens}")


if __name__ == "__main__":
    main()
