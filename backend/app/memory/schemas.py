"""Memory 的 Pydantic 模型：LLM 提取结果、检索结果、HTTP 请求/响应。"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class MemoryType(str, Enum):
    """记忆类型（数据库 memories.type 列的取值）。"""

    SEMANTIC = "semantic"
    EPISODIC = "episodic"


class MemoryActionType(str, Enum):
    """提取结果里的动作。

    本次只实现 CREATE / UPSERT；IGNORE 表示 LLM 看过但认为不值得记，
    apply() 遇到就跳过。UPDATE / DELETE 留到后续 Phase。
    """

    CREATE = "CREATE"
    UPSERT = "UPSERT"
    IGNORE = "IGNORE"


class MemoryAction(BaseModel):
    """LLM 从一轮对话里提取出的一条记忆变更意图。"""

    action: MemoryActionType
    type: MemoryType
    # semantic 用
    key: str | None = None
    value: str | None = None
    # episodic 用
    summary: str | None = None
    event_type: str | None = None

    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)

    @field_validator("importance", "confidence", mode="before")
    @classmethod
    def _clamp_ratio(cls, value):
        """LLM 偶尔返回 8 或 1.5 这种越界值。

        直接报 ValidationError 会让整轮提取全丢，代价太大；
        夹回 0~1 只是让排序里这一项满格，影响可控。
        """
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, number))

    def is_usable(self) -> bool:
        """这条意图能不能落库：IGNORE 不要，字段不全的也不要。"""
        if self.action == MemoryActionType.IGNORE:
            return False
        if self.type == MemoryType.SEMANTIC:
            return bool(self.key and self.value)
        return bool(self.summary)


class ExtractionResult(BaseModel):
    """LLM 提取的完整输出，对应 prompt 里要求的 JSON 结构。"""

    memories: list[MemoryAction] = Field(default_factory=list)


class MemoryRead(BaseModel):
    """一条记忆的读模型：API 响应与检索结果共用。

    score 只有检索结果会填，列表接口为 None。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    session_id: int | None = None
    type: MemoryType
    key: str | None = None
    value: str | None = None
    summary: str | None = None
    event_type: str | None = None
    importance: float = 0.5
    confidence: float = 1.0
    source: str = "llm_extraction"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_accessed_at: datetime | None = None
    score: float | None = None

    @computed_field
    @property
    def content(self) -> str:
        """可读正文：semantic 取 value，episodic 取 summary。"""
        if self.type == MemoryType.SEMANTIC:
            return self.value or ""
        return self.summary or ""


# ---------------------------------------------------------------------------
# HTTP 请求 / 响应
# ---------------------------------------------------------------------------


class MemoryCreateRequest(BaseModel):
    """用户在记忆页手动新增一条记忆。"""

    type: MemoryType = MemoryType.SEMANTIC
    # semantic 的锚点。留空时后端按内容生成一个稳定 key。
    key: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=2000)
    summary: str | None = Field(default=None, max_length=2000)
    event_type: str | None = Field(default=None, max_length=40)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryUpdateRequest(BaseModel):
    """修改记忆正文。semantic 改 value，episodic 改 summary。"""

    content: str = Field(min_length=1, max_length=2000)


class MemoryListResponse(BaseModel):
    success: bool
    memories: list[MemoryRead]


class MemoryMutationResponse(BaseModel):
    success: bool
    memory: MemoryRead
