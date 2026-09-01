"""Chat 域包：会话 / 消息 / 工具调用循环的完整业务域。

从旧的 services + routers 技术分层收拢而来，域内自包含：
    router               HTTP 端点
    sessions / branch    会话与分支服务
    messages             消息编排（发送 / 流式 / 修改 / 删除）
    message_context      上下文构建（历史 / 记忆 / RAG 注入）
    message_persistence  消息落库
    llm                  AI 客户端调用入口
    tool_calling         工具调用主循环（在 agent 域，普通聊天与 Agent 共用）

跨域依赖（core.cache / memory / rag / user / tools）保持单向。
"""

from ...agent.tool_calling import execute_tool_call, run_tool_loop, stream_with_tools
from .llm import chat_with_ai, chat_with_ai_stream
from .message_context import load_chat_context, save_chat_context
from .message_persistence import persist_assistant_message, persist_user_message
