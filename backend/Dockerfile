# 后端镜像：api 服务与 celery worker 共用（compose 中以不同 command 启动）
# 构建上下文为项目根目录（见 docker-compose.yml 的 build.context）
FROM python:3.12.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先复制依赖清单单独成层，代码变动时不重新安装依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 后端源码（tests / examples / uploads / .env 已被根 .dockerignore 排除）
COPY backend ./backend

# 非 root 运行：创建低权限用户；uploads 目录先创建并归属 appuser，
# 使 backend-uploads 命名卷首次挂载时继承正确属主（copy-on-first-use）
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/backend/uploads \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# compose 中 backend 服务会先执行 alembic 迁移再启动；此处为镜像默认命令
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
