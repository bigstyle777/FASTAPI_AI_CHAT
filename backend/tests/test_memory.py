"""
memory 模块测试脚本

用法（在 backend 目录下运行）：
    python tests/test_memory.py

说明：
- 通过 get_user_ai_settings + get_client 连接个人设置（或 .env 兜底）里的 client 和 model
- 可选环境变量 TEST_MEMORY_USERNAME 指定测试用户，
  默认取第一个在个人中心配置了 API Key 的用户
"""

import os
import sys
from pathlib import Path

# 保证无论从哪个目录运行，都能找到 backend 下的 app 包
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import SessionLocal
from app.models import User, UserSetting
from app.services.ai_client import get_client, get_user_ai_settings
from app.services.memory import (
    create_memory_service,
    delete_memory_service,
    get_memories_service,
)
from app.services.memory_extraction import (
    extract_memory,
    extract_memory_for_user,
)
from sqlalchemy import select

# 覆盖不同场景的测试消息
TEST_MESSAGES = [
    "我最近正在学习 RAG 和 Agent，之后想做一个知识库问答项目。",
    "今天中午吃了火锅，味道还不错。",
    "请记住：我的代码缩进偏好是 4 个空格。",
    "帮我算一下 123 乘以 456 等于多少。",
]


def pick_test_user(db):
    """优先按 TEST_MEMORY_USERNAME 找用户，否则取第一个配置了 API Key 的用户"""
    username = os.getenv("TEST_MEMORY_USERNAME")

    if username:
        user = db.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"用户 {username!r} 不存在，请检查 TEST_MEMORY_USERNAME")
        return user

    user = db.execute(
        select(User)
        .join(UserSetting, UserSetting.user_id == User.id)
        .where(UserSetting.api_key.isnot(None))
        .order_by(User.id)
        .limit(1)
    ).scalar_one_or_none()

    if user is None:
        raise SystemExit(
            "数据库里没有配置了 API Key 的用户，"
            "请先在个人中心保存设置，或设置 TEST_MEMORY_USERNAME"
        )

    return user


def test_extract_with_client(client, model):
    """直接用 setting 里的 client/model 测试记忆提取"""
    print("=" * 60)
    print("1. extract_memory（使用 setting 里的 client + model）")
    print("=" * 60)

    for message in TEST_MESSAGES:
        memories = extract_memory(client=client, model=model, message=message)
        print(f"\n消息: {message}")
        print(f"提取结果: {memories}")


def test_extract_for_user(user_id, db):
    """测试完整的 extract_memory_for_user 流程（内部自动读取用户设置）"""
    print("\n" + "=" * 60)
    print("2. extract_memory_for_user（自动读取用户 setting）")
    print("=" * 60)

    for message in TEST_MESSAGES[:2]:
        memories = extract_memory_for_user(message=message, user_id=user_id, db=db)
        print(f"\n消息: {message}")
        print(f"提取结果: {memories}")


def test_memory_crud(user_id, db):
    """测试 create / get / delete 服务层接口，结束后清理测试数据"""
    print("\n" + "=" * 60)
    print("3. memory 增删查服务")
    print("=" * 60)

    before = get_memories_service(db=db, user_id=user_id)
    print(f"\n当前记忆数量: {len(before)}")

    created = create_memory_service(
        db=db,
        user_id=user_id,
        content="[测试数据] 用户偏好使用 Python + FastAPI 技术栈。",
    )
    print(f"创建记忆: id={created.id}, content={created.content!r}")

    after = get_memories_service(db=db, user_id=user_id)
    print(f"创建后记忆数量: {len(after)}")
    assert len(after) == len(before) + 1, "创建后记忆数量应该 +1"
    assert any(m.id == created.id for m in after), "新记忆应该出现在列表里"

    try:
        result = delete_memory_service(db=db, user_id=user_id, memory_id=created.id)
        print(f"删除记忆: {result}")
        assert result["success"] is True

        final = get_memories_service(db=db, user_id=user_id)
        print(f"删除后记忆数量: {len(final)}")
        assert len(final) == len(before), "删除后记忆数量应该恢复"

        # 删除不存在的记忆应报错
        try:
            delete_memory_service(db=db, user_id=user_id, memory_id=created.id)
            raise AssertionError("重复删除应该抛出 ValueError")
        except ValueError as e:
            print(f"重复删除正确报错: {e}")

        print("\n增删查测试全部通过 ✔")
    finally:
        # 兜底清理：如果中途断言失败，也保证测试数据被删掉
        from app.crud import delete_memory

        delete_memory(db, created.id, user_id)


def main():
    db = SessionLocal()

    try:
        user = pick_test_user(db)

        # 连接 setting 里的 client 和 model（个人设置优先，.env 兜底）
        api_key, provider = get_user_ai_settings(user_id=user.id, db=db)
        result = get_client(api_key=api_key, provider=provider)

        if not result:
            raise SystemExit("无法创建 AI client：请先在个人中心或 .env 配置 API Key")

        client, model = result

        print(f"测试用户: {user.username} (id={user.id})")
        print(f"provider: {provider}")
        print(f"model: {model}")
        print(
            f"api_key: {(api_key or '')[:6]}...(已脱敏)"
            if api_key
            else "api_key: 使用 .env 兜底"
        )

        test_extract_with_client(client, model)
        test_extract_for_user(user.id, db)
        test_memory_crud(user.id, db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
