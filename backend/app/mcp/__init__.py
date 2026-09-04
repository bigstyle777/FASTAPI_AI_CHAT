"""MCP 接入包：连接生命周期、配置与异常，全部收敛在这里。

包对外（tools/mcp_adapter.py，唯一使用方）暴露:
    - mcp_manager: 模块级单例，lifespan 里 await connect_all / close_all
    - MCPToolError: 唯一对外异常类型（agent 层统一容错只认它）
    - MCPServerConfig / MCPClientManager: 多 server 配置与管理入口
"""

from .config import MCPServerConfig, get_mcp_server_configs, load_mcp_server_configs
from .errors import MCPToolError
from .manager import MCPClientManager, mcp_manager

__all__ = [
    "MCPClientManager",
    "MCPServerConfig",
    "MCPToolError",
    "get_mcp_server_configs",
    "load_mcp_server_configs",
    "mcp_manager",
]
