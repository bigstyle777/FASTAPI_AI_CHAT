"""Agent 运行时与 Memory 系统之间的契约（Phase 7）。

为什么需要这个文件
------------------
app/agent/ 是 Agent 的主场：它只依赖"记忆系统长什么样"的接口描述，
不 import app.memory 里的任何实现。反过来，MemoryManager（app/memory）
作为适配方只需让方法签名和行为满足这里的契约即可 —— 实现方与消费方
互不 import 具体实现，靠 Protocol 解耦。这就是"agent 对 memory 实现
保持无感知"的落点。

契约只有两个动作：
- retrieve：把和 query 相关的记忆取回来（多路召回 + 重排后的 Top K）；
- reinforce：确认一批记忆被真正用上（agent 把某条记忆写进了回答），
  memory 侧据此累加 access_count、上调 confidence、把退化中的记忆拉回。

返回的记录用 MemoryRecord 描述：这是 agent 允许读取的最小投影。
实现方返回的对象只要具备这些字段即可（本项目里 MemoryRead 天然满足），
不需要真的实例化 MemoryRecord —— 契约看重的是行为，不是类型。
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryRecord:
    """agent 眼里一条记忆的样子：id 用于 reinforce，其余用于拼 prompt。"""

    id: int
    type: str  # "semantic" | "episodic"
    key: str | None  # semantic 才有
    content: str  # 可读正文（semantic 取 value，episodic 取 summary）
    importance: float
    confidence: float
    score: float | None = None  # 检索排序分，非检索场景为 None


@runtime_checkable
class MemoryPort(Protocol):
    """Agent 侧依赖的 Memory 最小面。

    只声明方法签名，不含任何实现；MemoryManager 按同样的行为实现它。
    加方法要慎重：每多一个方法，实现方和测试就多一份义务。
    """

    async def retrieve(
        self, query: str, top_k: int | None = None
    ) -> list[MemoryRecord]:
        """检索与 query 最相关的记忆，按相关性降序。"""
        ...

    async def reinforce(self, memory_ids: list[int]) -> int:
        """确认一批记忆被真正用上，返回实际强化的条数。"""
        ...
