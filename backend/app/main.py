from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .routers import users
from .routers import chat
from .exceptions import BusinessError
from .core.database import init_db
from .core.redis import RedisUnavailableError


app = FastAPI()


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

init_db()

app.include_router(
    users.router
)
app.include_router(
    chat.router
)

@app.middleware("http")
async def log_request(request, call_next):
    print(f"{request.method} {request.url}")

    response = await call_next(request)

    return response

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
