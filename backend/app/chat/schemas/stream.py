from typing import Literal, Optional, Union

from pydantic import BaseModel


# token字段
class TokenUsage(BaseModel):
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class StreamDeltaEvent(BaseModel):
    type: Literal["delta"] = "delta"
    content: str


class StreamUsageEvent(BaseModel):
    type: Literal["usage"] = "usage"
    usage: TokenUsage


class StreamErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class StreamDoneEvent(BaseModel):
    type: Literal["done"] = "done"


StreamEvent = Union[
    StreamDeltaEvent,
    StreamUsageEvent,
    StreamErrorEvent,
    StreamDoneEvent,
]
