# 后端镜像：api 服务与 celery worker 共用（compose 中以不同 command 启动）
# 构建上下文为项目根目录（见 docker-compose.yml 的 build.context）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先复制依赖清单单独成层，代码变动时不重新安装依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 后端源码（tests / examples / uploads / .env 已被根 .dockerignore 排除）
COPY backend ./backend

EXPOSE 8000

# compose 中 backend 服务会先执行 alembic 迁移再启动；此处为镜像默认命令
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
