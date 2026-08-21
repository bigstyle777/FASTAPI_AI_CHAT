"""pytest 共享 fixture。

- 连独立测试库 `aichat_test`（可用环境变量 TEST_DATABASE_URL 覆盖），不污染主库。
- 每个用例结束后 TRUNCATE 所有表，保证用例之间隔离。
- mock 掉 Redis，让 session/message 服务测试不依赖外部缓存服务。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_session_message_services.py -v
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import Base  # noqa: E402
from app.crud import create_role, create_user, get_role_by_name  # noqa: E402

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/aichat_test",
)
ADMIN_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"


def _ensure_test_database() -> None:
    """测试库不存在时先创建（postgres 默认库上执行 CREATE DATABASE）。"""
    from sqlalchemy.engine import make_url

    db_name = make_url(TEST_DATABASE_URL).database
    admin_engine = create_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()


def _truncate_all(engine) -> None:
    # 直接遍历所有表，依赖 TRUNCATE ... CASCADE 的级联能力处理外键顺序，
    # 避免 sorted_tables 对 chat_sessions <-> messages 循环外键发出排序警告。
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(TEST_DATABASE_URL)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        _truncate_all(engine)


@pytest.fixture(autouse=True)
def _mock_redis(monkeypatch):
    """用内存 dict 替换 Redis 操作，避免测试依赖真实 Redis。

    返回 store dict；测试需要直接读/写键时可以按名请求本 fixture。
    消费模块统一通过 ``redis.xxx`` 模块限定访问（见 app/core/redis.py），
    所以只需 patch app.core.redis 一处，所有消费者自动生效。
    """
    import json as _json

    import app.core.redis as redis_mod

    store: dict = {}

    def redis_delete(*keys):
        for key in keys:
            store.pop(key, None)

    def redis_set(key, value, ttl=None):
        store[key] = value

    def redis_get(key):
        return store.get(key)

    def redis_set_json(key, value, ttl=None):
        store[key] = _json.dumps(value, ensure_ascii=False)

    def redis_get_json(key):
        raw = store.get(key)
        return _json.loads(raw) if raw is not None else None

    def redis_incr(key):
        store[key] = store.get(key, 0) + 1
        return store[key]

    def redis_expire(key, ttl=None):
        return None

    monkeypatch.setattr(redis_mod, "redis_delete", redis_delete)
    monkeypatch.setattr(redis_mod, "redis_set", redis_set)
    monkeypatch.setattr(redis_mod, "redis_get", redis_get)
    monkeypatch.setattr(redis_mod, "redis_set_json", redis_set_json)
    monkeypatch.setattr(redis_mod, "redis_get_json", redis_get_json)
    monkeypatch.setattr(redis_mod, "redis_incr", redis_incr)
    monkeypatch.setattr(redis_mod, "redis_expire", redis_expire)

    return store


# ---------------------------------------------------------------------------
# memory / planner 测试用 fixture（脱离真实 LLM 调用）
# ---------------------------------------------------------------------------


class _FakeChatCompletions:
    """伪 completions：任何 create 调用都返回确定性的空响应。"""

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="{}"),
                )
            ]
        )


@pytest.fixture
def model():
    """测试模型名（纯字符串，不依赖真实 provider）。"""
    return "test-model"


@pytest.fixture
def client():
    """伪 OpenAI 客户端，供 memory / planner 测试脱离真实 API 调用。"""
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeChatCompletions()))


@pytest.fixture
def user_id(db):
    """在测试库创建一个用户并返回其 id。"""
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    return create_user(db, "test_user", "hashed-password", role.id).id