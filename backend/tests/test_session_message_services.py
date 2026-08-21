"""session / message / branch 服务层关键路径测试。

重点覆盖重构中会改名的函数：
    delete_message_service（删单条） / clear_session_messages_service（清空会话）
    update_session_service（改名） / create_branch_service / create_message_branch_service
    以及 session 的增删查。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_session_message_services.py -v
"""

from types import SimpleNamespace

import pytest

from app.crud import (
    create_message,
    create_role,
    create_session,
    create_user,
    get_messages_by_session,
    get_role_by_name,
    get_session_by_user,
)
from app.exceptions import BusinessError
from app.services.branch import create_branch_service, create_message_branch_service
from app.services.messages import delete_message_service
from app.services.sessions import (
    clear_session_messages_service,
    create_session_service,
    delete_session_service,
    get_sessions_service,
    update_session_service,
)


def _make_user(db, username="alice"):
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    return create_user(db, username, "hashed-password", role.id)


def _make_session(db, user_id, title="新会话"):
    return create_session(db, user_id, title)


def _user_ctx(user_id):
    return {"user_id": user_id}


# ---------------------------------------------------------------------------
# session CRUD
# ---------------------------------------------------------------------------


def test_create_and_delete_session_roundtrip(db):
    user = _make_user(db)
    result = create_session_service(
        db, _user_ctx(user.id), SimpleNamespace(title="  聊聊 FastAPI  ")
    )
    assert result["success"] is True
    session_id = result["session_id"]

    session = get_session_by_user(db, session_id, user.id)
    assert session is not None
    assert session.title == "聊聊 FastAPI"

    deleted = delete_session_service(db, _user_ctx(user.id), session_id)
    assert deleted["success"] is True
    assert get_session_by_user(db, session_id, user.id) is None


def test_create_session_blank_title_uses_default(db):
    user = _make_user(db)
    result = create_session_service(db, _user_ctx(user.id), SimpleNamespace(title="  "))
    session = get_session_by_user(db, result["session_id"], user.id)
    assert session.title == "新会话"


def test_update_session_renames(db):
    user = _make_user(db)
    session = _make_session(db, user.id, "旧标题")
    req = SimpleNamespace(title="新标题", is_pinned=None)
    result = update_session_service(db, _user_ctx(user.id), session.id, req)
    assert result["title"] == "新标题"
    assert get_session_by_user(db, session.id, user.id).title == "新标题"


def test_update_session_empty_title_rejected(db):
    user = _make_user(db)
    session = _make_session(db, user.id, "旧标题")
    req = SimpleNamespace(title="   ", is_pinned=None)
    with pytest.raises(BusinessError):
        update_session_service(db, _user_ctx(user.id), session.id, req)


# ---------------------------------------------------------------------------
# message 删除
# ---------------------------------------------------------------------------


def test_delete_message_service_removes_user_and_assistant_pair(db):
    user = _make_user(db)
    session = _make_session(db, user.id)
    user_msg = create_message(db, session.id, "user", "你好")
    create_message(db, session.id, "assistant", "你好，有什么可以帮你？")

    result = delete_message_service(db, _user_ctx(user.id), user_msg.id)
    assert result["success"] is True

    remaining = get_messages_by_session(db, session.id)
    assert len(remaining) == 0


def test_delete_message_service_removes_assistant_only(db):
    user = _make_user(db)
    session = _make_session(db, user.id)
    create_message(db, session.id, "user", "你好")
    assistant_msg = create_message(db, session.id, "assistant", "回复")

    delete_message_service(db, _user_ctx(user.id), assistant_msg.id)

    remaining = get_messages_by_session(db, session.id)
    assert len(remaining) == 1
    assert remaining[0].role == "user"


def test_delete_messages_service_clears_session(db):
    user = _make_user(db)
    session = _make_session(db, user.id)
    create_message(db, session.id, "user", "a")
    create_message(db, session.id, "assistant", "b")

    result = clear_session_messages_service(db, _user_ctx(user.id), session.id)
    assert result["success"] is True
    assert result["deleted_count"] == 2
    assert result["session_deleted"] is True
    assert get_session_by_user(db, session.id, user.id) is None


# ---------------------------------------------------------------------------
# 分支
# ---------------------------------------------------------------------------


def test_create_branch_service_copies_messages(db):
    user = _make_user(db)
    session = _make_session(db, user.id, "主会话")
    create_message(db, session.id, "user", "问题")
    create_message(db, session.id, "assistant", "答案")

    result = create_branch_service(db, _user_ctx(user.id), session.id)
    branch_id = result["session_id"]

    branch = get_session_by_user(db, branch_id, user.id)
    assert branch is not None
    assert branch.title == "分支·主会话"

    messages = get_messages_by_session(db, branch_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert [m.content for m in messages] == ["问题", "答案"]


def test_create_message_branch_service(db):
    user = _make_user(db)
    session = _make_session(db, user.id, "主会话")
    user_msg = create_message(db, session.id, "user", "原始问题")

    result = create_message_branch_service(db, _user_ctx(user.id), user_msg.id)
    assert result["session_id"] != session.id
    assert result["title"].startswith("分支·")

    branch = get_session_by_user(db, result["session_id"], user.id)
    assert branch is not None
    assert branch.branch_from_message_id == user_msg.id