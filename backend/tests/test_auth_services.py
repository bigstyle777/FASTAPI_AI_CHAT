"""auth / captcha / settings / rbac 服务层测试（不依赖真实 Redis / LLM）。

覆盖：
    register_user / login_user / logout_user / create_login_session
    resolve_current_user_context（无效 token / 已注销 / 正常）
    get_user_profile_service
    验证码 创建→校验 闭环（错误码、一次性）
    个人设置 读缓存 / DB 回退 / 默认值 / 保存往返
    RBAC 默认数据同步幂等 / require_permissions / ensure_bootstrap_admin

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_auth_services.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jose import jwt

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.core.security import verify_password  # noqa: E402
from app.crud import get_user_by_username  # noqa: E402
from app.services.auth import (  # noqa: E402
    login_user,
    logout_user,
    register_user,
    resolve_current_user_context,
)
from app.services.captcha import create_captcha_service  # noqa: E402
from app.services.rbac import (  # noqa: E402
    ensure_bootstrap_admin,
    require_permissions,
    set_role_permissions,
    sync_default_rbac,
)
from app.services.settings import (  # noqa: E402
    get_settings_service,
    save_settings_service,
)


def _register_request(username="alice", password="secret123"):
    return SimpleNamespace(username=username, password=password)


def _login_request(username, password, captcha_id, captcha_code):
    return SimpleNamespace(
        username=username,
        password=password,
        captcha_id=captcha_id,
        captcha_code=captcha_code,
    )


def _login_with_valid_captcha(db, _mock_redis, username, password):
    captcha = create_captcha_service()
    code = _mock_redis[f"auth:captcha:{captcha['captcha_id']}"]
    return login_user(db, _login_request(username, password, captcha["captcha_id"], code))


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------


def test_register_user_creates_user_with_default_role(db):
    result = register_user(db, _register_request())
    assert result == {"success": True, "message": "registration successful"}

    user = get_user_by_username(db, "alice")
    assert user is not None
    assert user.role.name == "user"
    assert verify_password("secret123", user.password)


def test_register_user_rejects_duplicate_username(db):
    register_user(db, _register_request())
    result = register_user(db, _register_request())
    assert result["success"] is False


# ---------------------------------------------------------------------------
# 登录 / 注销 / token 解析
# ---------------------------------------------------------------------------


def test_login_rejects_wrong_captcha(db, _mock_redis):
    register_user(db, _register_request())
    captcha = create_captcha_service()
    result = login_user(
        db, _login_request("alice", "secret123", captcha["captcha_id"], "XXXXX")
    )
    assert result["success"] is False
    assert result["message"] == "invalid captcha"


def test_login_rejects_unknown_user(db, _mock_redis):
    result = _login_with_valid_captcha(db, _mock_redis, "ghost", "secret123")
    assert result["success"] is False
    assert result["message"] == "user not found"


def test_login_rejects_wrong_password(db, _mock_redis):
    register_user(db, _register_request())
    result = _login_with_valid_captcha(db, _mock_redis, "alice", "wrong-pass")
    assert result["success"] is False
    assert result["message"] == "invalid password"


def test_login_success_returns_resolvable_token(db, _mock_redis):
    register_user(db, _register_request())
    result = _login_with_valid_captcha(db, _mock_redis, "alice", "secret123")

    assert result["success"] is True
    assert result["token_type"] == "bearer"

    user = resolve_current_user_context(db, result["access_token"])
    assert user["username"] == "alice"
    assert user["role"] == "user"
    assert "chat:read" in user["permissions"]


def test_logout_revokes_token(db, _mock_redis):
    register_user(db, _register_request())
    result = _login_with_valid_captcha(db, _mock_redis, "alice", "secret123")
    token = result["access_token"]

    logout_user(token)

    with pytest.raises(HTTPException) as exc_info:
        resolve_current_user_context(db, token)
    assert exc_info.value.status_code == 401


def test_resolve_rejects_garbage_token(db):
    with pytest.raises(HTTPException) as exc_info:
        resolve_current_user_context(db, "not-a-jwt")
    assert exc_info.value.status_code == 401


def test_resolve_rejects_forged_token(db, _mock_redis):
    payload = {"user_id": 1, "username": "alice"}
    forged = jwt.encode(payload, "wrong-secret", algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as exc_info:
        resolve_current_user_context(db, forged)
    assert exc_info.value.status_code == 401


def test_resolve_rejects_token_without_session(db, _mock_redis):
    """JWT 合法但会话已不在 Redis（如重启丢数据）也应 401。"""
    register_user(db, _register_request())
    result = _login_with_valid_captcha(db, _mock_redis, "alice", "secret123")
    token = result["access_token"]

    _mock_redis.clear()

    with pytest.raises(HTTPException) as exc_info:
        resolve_current_user_context(db, token)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 验证码一次性
# ---------------------------------------------------------------------------


def test_captcha_is_single_use(db, _mock_redis):
    register_user(db, _register_request())
    captcha = create_captcha_service()
    captcha_id = captcha["captcha_id"]
    code = _mock_redis[f"auth:captcha:{captcha_id}"]

    first = login_user(db, _login_request("alice", "secret123", captcha_id, code))
    assert first["success"] is True

    # 同一验证码不能重复使用（重放）
    second = login_user(db, _login_request("alice", "secret123", captcha_id, code))
    assert second["success"] is False


# ---------------------------------------------------------------------------
# 个人设置
# ---------------------------------------------------------------------------


def _user_ctx(db, username="carol"):
    from app.crud import create_role, create_user, get_role_by_name

    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    user = create_user(db, username, "hashed-password", role.id)
    return {"user_id": user.id}


def test_get_settings_returns_defaults_without_row(db):
    ctx = _user_ctx(db)
    result = get_settings_service(db, ctx)
    assert result["success"] is True
    assert result["api_key"] is None
    assert result["provider"] == "deepseek"
    assert result["embedding_model"] == settings.rag_embedding_model


def test_get_settings_reads_saved_row_and_caches(db, _mock_redis):
    ctx = _user_ctx(db)
    save_settings_service(
        db,
        ctx,
        SimpleNamespace(
            api_key="sk-test",
            provider="openai",
            embedding_api_key="",
            embedding_base_url="",
            embedding_model="",
        ),
    )
    # 清掉 save 写入的缓存，验证 DB 回退路径
    _mock_redis.clear()

    result = get_settings_service(db, ctx)
    assert result["api_key"] == "sk-test"
    assert result["provider"] == "openai"

    # DB 回退后应回写缓存
    assert any(key.startswith("user:settings:") for key in _mock_redis)


def test_get_settings_prefers_cache_over_db(db, _mock_redis):
    ctx = _user_ctx(db)
    save_settings_service(
        db,
        ctx,
        SimpleNamespace(
            api_key="sk-db",
            provider="deepseek",
            embedding_api_key="",
            embedding_base_url="",
            embedding_model="",
        ),
    )
    # 直接改缓存里的值，get 应返回缓存而不是 DB
    cache_key = next(key for key in _mock_redis if key.startswith("user:settings:"))
    _mock_redis[cache_key] = '{"api_key": "sk-cache", "provider": "deepseek"}'

    result = get_settings_service(db, ctx)
    assert result["api_key"] == "sk-cache"


def test_save_settings_roundtrip_updates_db_and_cache(db, _mock_redis):
    ctx = _user_ctx(db)
    result = save_settings_service(
        db,
        ctx,
        SimpleNamespace(
            api_key="sk-new",
            provider="deepseek",
            embedding_api_key="emb-key",
            embedding_base_url="https://emb.example.com/v1",
            embedding_model="bge-m3",
        ),
    )
    assert result["success"] is True
    assert result["api_key"] == "sk-new"
    assert result["embedding_model"] == "bge-m3"

    # 后续读取（走缓存）应与保存结果一致
    cached = get_settings_service(db, ctx)
    assert cached["api_key"] == "sk-new"
    assert cached["embedding_base_url"] == "https://emb.example.com/v1"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def _snapshot_rbac(db):
    from app.crud import get_roles

    return {
        role.name: sorted(p.code for p in role.permissions if p) for role in get_roles(db)
    }


def test_sync_default_rbac_is_idempotent(db):
    sync_default_rbac(db)
    before = _snapshot_rbac(db)

    sync_default_rbac(db)
    after = _snapshot_rbac(db)

    assert before == after
    assert "admin:access" in after["admin"]
    assert set(after["user"]) == {"chat:read", "chat:write", "settings:write"}


def test_require_permissions_allows_and_denies():
    dependency = require_permissions("admin:access")

    admin = {"user_id": 1, "role": "admin", "permissions": ["admin:access", "chat:read"]}
    assert dependency(admin) is admin

    user = {"user_id": 2, "role": "user", "permissions": ["chat:read"]}
    with pytest.raises(HTTPException) as exc_info:
        dependency(user)
    assert exc_info.value.status_code == 403


def test_set_role_permissions_rejects_unknown_code(db):
    sync_default_rbac(db)
    from app.crud import get_role_by_name

    role = get_role_by_name(db, "user")
    with pytest.raises(HTTPException) as exc_info:
        set_role_permissions(db, role.id, ["chat:read", "bogus:code"])
    assert exc_info.value.status_code == 400


def test_set_role_permissions_replaces(db):
    sync_default_rbac(db)
    from app.crud import get_role_by_name

    role = get_role_by_name(db, "user")
    set_role_permissions(db, role.id, ["chat:read"])

    db.refresh(role)
    assert sorted(p.code for p in role.permissions) == ["chat:read"]


def test_ensure_bootstrap_admin_creates_then_updates(db):
    created = ensure_bootstrap_admin(db, "root", "root-password-1")
    assert created is not None
    assert created.role.name == "admin"
    assert verify_password("root-password-1", created.password)

    updated = ensure_bootstrap_admin(db, "root", "root-password-2")
    assert updated.id == created.id
    assert verify_password("root-password-2", updated.password)


def test_ensure_bootstrap_admin_noop_without_credentials(db):
    assert ensure_bootstrap_admin(db, None, None) is None
    assert ensure_bootstrap_admin(db, "admin", "") is None
