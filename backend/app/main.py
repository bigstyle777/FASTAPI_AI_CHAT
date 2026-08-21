import logging
import secrets
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agent.router import router as agent_router
from .core.config import settings
from .core.database import SessionLocal
from .core.logging import configure_logging, new_request_id, request_id_var
from .core.redis import RedisUnavailableError
from .exceptions import BusinessError
from .rag.router import router as rag_router
from .routers import admin, chat, memory, users
from .services.auth import resolve_current_user_context
from .services.rbac import ensure_bootstrap_admin, sync_default_rbac

# 尽早配置：uvicorn 导入本模块时接管日志格式，后续所有模块的 logger 统一生效
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化安全配置，关闭时清理资源。"""
    _validate_jwt_secret()
    with SessionLocal() as db:
        sync_default_rbac(db)
        ensure_bootstrap_admin(
            db,
            settings.bootstrap_admin_username,
            settings.bootstrap_admin_password,
        )
    logger.info("应用启动完成，安全初始化已执行")
    yield


def _validate_jwt_secret():
    """启动时检测 JWT 密钥安全性。默认弱密钥会生成随机密钥并警告。"""
    if settings.is_default_jwt_secret:
        random_secret = secrets.token_urlsafe(48)
        settings.jwt_secret_key = random_secret
        logger.warning(
            "\n"
            "=" * 70 + "\n"
            "[安全警告] JWT_SECRET_KEY 使用了不安全的默认值！\n"
            "本次启动已生成随机密钥，但服务重启后登录态将失效。\n"
            "请在 .env 中设置一个强随机密钥（至少 32 字符），例如：\n"
            f"  JWT_SECRET_KEY={secrets.token_urlsafe(32)}\n" + "=" * 70
        )


app = FastAPI(lifespan=lifespan)
ADMIN_PATH_PREFIX = "/admin"


def _extract_bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token or None


@app.get("/")
def root():
    return {"message": "AI Chat Pro API is running", "docs": "/docs"}


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(rag_router)
app.include_router(agent_router)
app.include_router(users.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(admin.router)


async def _authenticate_request(request: Request) -> JSONResponse | None:
    """预解析 Bearer token；admin 路径未认证时直接返回 401 响应。"""
    token = _extract_bearer_token(request)
    if token:
        with SessionLocal() as db:
            try:
                current_user = resolve_current_user_context(db, token)
                request.state.current_user = {"token": token, "user": current_user}
            except Exception:
                request.state.current_user = None
                if request.url.path.startswith(ADMIN_PATH_PREFIX):
                    return JSONResponse(
                        status_code=401,
                        content={"success": False, "message": "INVALID TOKEN"},
                    )
    elif request.url.path.startswith(ADMIN_PATH_PREFIX):
        return JSONResponse(
            status_code=401,
            content={"success": False, "message": "AUTHENTICATION REQUIRED"},
        )
    return None


def _resolved_user_id(request: Request) -> int | None:
    current_user = getattr(request.state, "current_user", None)
    if current_user and "user" in current_user:
        return current_user["user"].get("user_id")
    return None


@app.middleware("http")
async def log_request(request, call_next):
    # 每个请求一个 request_id，贯穿本请求内所有日志行（含 SSE 流式生成器）
    token = request_id_var.set(new_request_id())
    started_at = perf_counter()
    try:
        response = await _authenticate_request(request)
        if response is None:
            response = await call_next(request)

        logger.info(
            "access %s %s status=%s duration_ms=%d user_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            int((perf_counter() - started_at) * 1000),
            _resolved_user_id(request),
        )
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    return JSONResponse(
        status_code=400, content={"success": False, "message": str(exc.message)}
    )


@app.exception_handler(RedisUnavailableError)
async def redis_error_handler(request: Request, exc: RedisUnavailableError):
    return JSONResponse(
        status_code=503,
        content={"success": False, "message": "Redis 不可用，请检查 Redis 服务"},
    )
