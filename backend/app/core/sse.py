import json
from typing import Any

from pydantic import BaseModel


def sse_event(event: str, data: BaseModel | dict[str, Any]) -> str:
    if isinstance(data, BaseModel):
        payload = data.model_dump_json()
    else:
        payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
