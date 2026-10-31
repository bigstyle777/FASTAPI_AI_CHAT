"""services 层共享的会话文案常量。"""

DEFAULT_SESSION_TITLE = "新会话"
BRANCH_TITLE_PREFIX = "分支·"

# 向量列的固定维度。模型声明和迁移都以此为基准，
# 防止"改了 settings 没改迁移"导致 alembic 误判或运行时插入失败。
# 启动时 lifespan 会校验 settings.rag_embedding_dimension 与此一致。
EMBEDDING_DIMENSION = 1024