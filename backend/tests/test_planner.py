r"""
agent.planner 模块测试脚本

用法（在 backend 目录下运行）：
    ..\.venv\Scripts\python.exe tests\test_planner.py

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

from sqlalchemy import select

from app.agent.planner import create_plan
from app.core.database import SessionLocal
from app.models import User, UserSetting
from app.services.ai_client import get_client, get_user_ai_settings

# 覆盖不同场景的测试任务（对应 weather / calculator / web_search 工具）
TEST_TASKS = [
    # 多步骤 + 两个工具串联
    "查询北京今天的天气，然后把温度乘以 2",
    # 单工具
    "帮我算一下 (123 + 456) * 7.5",
    # 搜索类
    "搜索一下 FastAPI 最新版本有什么新特性",
    # 无工具，纯问答
    "用一句话解释什么是 RAG",
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


def test_create_plan(client, model):
    """测试 create_plan：调用真实 LLM 生成计划，并打印解析后的步骤"""
    print("=" * 60)
    print("create_plan（使用 setting 里的 client + model）")
    print("=" * 60)

    for task in TEST_TASKS:
        messages = [{"role": "user", "content": task}]
        plan = create_plan(client, model, messages)

        print(f"\n任务: {task}")
        print(f"解析成功，共 {len(plan)} 个步骤:")
        for i, step in enumerate(plan, 1):
            print(f"  {i}. [{step.tool or '-'}] {step.description}")


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
        print(f"api_key: {(api_key or '')[:6]}...(已脱敏)" if api_key else "api_key: 使用 .env 兜底")

        test_create_plan(client, model)
    finally:
        db.close()


if __name__ == "__main__":
    main()
