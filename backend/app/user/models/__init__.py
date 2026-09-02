"""User 域 ORM 模型。

子模块之间存在交叉引用的 relationship（字符串形式），
所有域的模型包都会被 alembic env / conftest 统一导入后再配置 mapper。
"""

from .user_setting import User, UserSetting

__all__ = ["User", "UserSetting"]
