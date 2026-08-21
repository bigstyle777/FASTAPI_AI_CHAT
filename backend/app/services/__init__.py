"""业务服务层。

各模块按领域拆分（auth / messages / sessions / memory / ...），
调用方直接从子模块导入：``from app.services.auth import get_current_user``。
"""
