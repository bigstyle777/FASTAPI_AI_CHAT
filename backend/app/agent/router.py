"""Agent HTTP 接口。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas import ChatRequest
from ..services.auth import get_current_user
from .repo import get_agent_run, list_agent_runs
from .schemas import AgentRunListResponse, AgentRunResponse
from .service import agent_stream_service

router = APIRouter(prefix="/agent", tags=["Agent"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


@router.post("/stream")
def agent_stream(request: ChatRequest, user: CurrentUser, db: Database):
    """流式运行一个 agent：规划 -> 逐步执行 -> 总结，全程 SSE 推送。"""
    return StreamingResponse(
        agent_stream_service(db, user, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs", response_model=AgentRunListResponse)
def list_runs(
    user: CurrentUser,
    db: Database,
    session_id: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """查看当前用户的 agent 运行记录（含 trace 明细，可按会话过滤）。"""
    runs = list_agent_runs(db, user["user_id"], session_id=session_id, limit=limit)
    items = []
    for run in runs:
        item = AgentRunResponse.model_validate(run)
        item.trace_count = len(item.traces)
        items.append(item)
    return {"success": True, "runs": items}


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def get_run(run_id: int, user: CurrentUser, db: Database):
    """查看一次 agent 运行的完整 trace，用于排错。"""
    run = get_agent_run(db, run_id, user_id=user["user_id"])
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    response = AgentRunResponse.model_validate(run)
    response.trace_count = len(response.traces)
    return response
