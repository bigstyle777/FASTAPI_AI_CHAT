"""
MCP 工具适配层：tools 包里唯一感知 app.mcp 的模块。

agent 包只认识 ALL_TOOLS / TOOL_REGISTRY，对 MCP 零感知——
所有 MCP 细节（连接、发现、路由）都收敛在 app/mcp/ 包里，
这里只做"格式衔接"：把 manager 产出的 schema 和可调用对象
合并进现有工具体系。
"""

import logging

from ..mcp import mcp_manager

logger = logging.getLogger(__name__)


async def register_mcp_tools(all_tools: list[dict], tool_registry: dict) -> list[str]:
    """连接所有 MCP server 并把发现的工具注册进现有体系。

    应用 lifespan 启动期调用一次：

    - schema 追加进 all_tools（即 app.tools.ALL_TOOLS）
    - 可调用对象放进 tool_registry（即 app.tools.TOOL_REGISTRY）
    - 与已有工具重名时跳过（先注册者优先），不静默覆盖

    任何失败都只记日志并返回已注册的部分（可能是空列表），
    不让 MCP 问题拖垮应用启动——本地工具始终可用。
    """
    try:
        await mcp_manager.connect_all()
        schemas = mcp_manager.get_openai_tools_schema()
        registry = mcp_manager.get_tool_registry()
    except Exception:  # noqa: BLE001
        logger.exception("MCP Server 连接失败，MCP 工具不可用")
        return []

    registered: list[str] = []
    for schema in schemas:
        name = schema["function"]["name"]
        if name in tool_registry:
            logger.warning("MCP 工具 '%s' 与已有工具重名，已跳过（保留原工具）", name)
            continue
        all_tools.append(schema)
        tool_registry[name] = registry[name]
        registered.append(name)

    logger.info("已注册 MCP 工具: %s", registered or "无")
    return registered
