from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.config import settings
from .core.database import SessionLocal
from .core.redis import RedisUnavailableError
from .exceptions import BusinessError
from .rag.router import router as rag_router
from .routers import admin, chat, users
from .services.auth import resolve_current_user_context
from .services.rbac import ensure_bootstrap_admin, sync_default_rbac

app = FastAPI()
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(rag_router)
app.include_router(users.router)
app.include_router(chat.router)
app.include_router(admin.router)


@app.middleware("http")
async def log_request(request, call_next):
    print(f"{request.method} {request.url}")

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

    response = await call_next(request)

    return response


@app.on_event("startup")
def initialize_security():
    with SessionLocal() as db:
        sync_default_rbac(db)
        ensure_bootstrap_admin(
            db,
            settings.bootstrap_admin_username,
            settings.bootstrap_admin_password,
        )


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
