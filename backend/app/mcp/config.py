"""MCP Server 配置：server 列表从 settings 读取，天然支持多 server。

配置格式（settings.mcp_servers，JSON 数组字符串，可用环境变量
MCP_SERVERS 覆盖）::

    [{"name": "calculator", "args": ["mcp_servers/calculator_server.py"]},
     {"name": "weather", "command": "uvx", "args": ["mcp-weather"], "enabled": false}]

字段说明:
    name:    server 名称，用于日志、路由和健康状态（不可重复）
    command: 启动命令，默认当前 Python 解释器
    args:    命令参数；指向项目内文件的相对路径按项目根解析
    env:     传给子进程的额外环境变量（可选）
    enabled: 是否启用（默认 True），置 False 可临时摘掉某个 server

settings 里不配置时，默认接入 calculator server，与历史行为一致。
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import settings

logger = logging.getLogger(__name__)

# app/mcp/config.py -> app/mcp -> app -> backend -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_SERVERS_JSON = json.dumps(
    [{"name": "calculator", "args": ["mcp_servers/calculator_server.py"]}]
)


@dataclass
class MCPServerConfig:
    """一个 MCP Server 的启动配置"""

    name: str  # 用于日志、路由和健康状态，例如 "calculator"
    command: str = field(default_factory=lambda: sys.executable)  # 例如 "python"
    args: list[str] = field(default_factory=list)  # 例如 ["mcp_servers/calculator_server.py"]
    env: dict[str, str] | None = None  # 传给子进程的额外环境变量
    enabled: bool = True  # 置 False 可临时摘掉某个 server

    def resolved_args(self) -> list[str]:
        """把指向项目内文件的相对参数解析成绝对路径，避免依赖运行时工作目录。

        只改写"确实指向项目根下已存在文件"的参数，普通 flag 原样保留。
        """
        return [_resolve_arg(arg) for arg in self.args]


def _resolve_arg(arg: str) -> str:
    path = Path(arg)
    if path.is_absolute():
        return arg
    candidate = PROJECT_ROOT / path
    return str(candidate) if candidate.exists() else arg


def _to_config(item: Any) -> MCPServerConfig | None:
    """把一个 JSON 条目转成 MCPServerConfig；无效条目记日志并返回 None。"""
    if not isinstance(item, dict):
        logger.error("MCP server 配置条目必须是对象，已跳过: %r", item)
        return None

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        logger.error("MCP server 配置缺少有效 name，已跳过: %r", item)
        return None

    args = item.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        logger.error("MCP server '%s' 的 args 必须是字符串数组，已跳过", name)
        return None

    env = item.get("env") or None
    if env is not None and not isinstance(env, dict):
        logger.error("MCP server '%s' 的 env 必须是对象，已忽略", name)
        env = None

    return MCPServerConfig(
        name=name,
        command=item.get("command") or sys.executable,
        args=list(args),
        env=env,
        enabled=bool(item.get("enabled", True)),
    )


def load_mcp_server_configs(raw_json: str | None = None) -> list[MCPServerConfig]:
    """解析 server 配置列表；未配置（None）时返回默认的 calculator server。

    配置是外部输入，解析问题只告警不抛出，保证不影响应用启动：
    无效条目跳过、同名 server 保留先出现的、显式禁用的跳过。
    """
    raw = raw_json if raw_json is not None else DEFAULT_SERVERS_JSON
    if not raw.strip():
        return []

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("MCP_SERVERS 配置不是合法 JSON，已忽略: %s", raw)
        return []
    if not isinstance(items, list):
        logger.error("MCP_SERVERS 配置必须是 JSON 数组，已忽略")
        return []

    configs: list[MCPServerConfig] = []
    seen: set[str] = set()
    for item in items:
        config = _to_config(item)
        if config is None:
            continue
        if not config.enabled:
            logger.info("MCP server '%s' 已禁用，跳过", config.name)
            continue
        if config.name in seen:
            logger.warning("MCP server 配置重名: '%s'，保留先出现的", config.name)
            continue
        seen.add(config.name)
        configs.append(config)
    return configs


def get_mcp_server_configs() -> list[MCPServerConfig]:
    """从 settings 读取 server 列表（settings.mcp_servers -> MCPServerConfig）。"""
    return load_mcp_server_configs(settings.mcp_servers)
