"""ORM 模型。

按领域拆分到子模块，这里统一再导出，保持 ``from app.models import X`` 的既有调用方式不变。

注意：子模块之间存在交叉引用的 relationship（字符串形式），
必须在本包 ``__init__`` 里全部导入，SQLAlchemy 才能在 mapper 配置期解析。
"""

from ..core.database import Base
from .agent import AgentRun, AgentTracePoint
from .chat import ChatSession, Message
from .memory import UserMemory, UserMemoryEmbedding
from .rag import RagChunk, RagChunkEmbedding, RagDocument
from .rbac import Permission, Role, RolePermission
from .user import User, UserSetting

__all__ = [
    "AgentRun",
    "AgentTracePoint",
    "Base",
    "ChatSession",
    "Message",
    "Permission",
    "RagChunk",
    "RagChunkEmbedding",
    "RagDocument",
    "Role",
    "RolePermission",
    "User",
    "UserMemory",
    "UserMemoryEmbedding",
    "UserSetting",
]
