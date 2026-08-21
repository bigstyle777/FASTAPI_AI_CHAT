#!/usr/bin/env bash
# PostgreSQL 备份脚本（docker compose 环境）
# 用法：bash docker/backup.sh
# 定时调度（可选）：
#   - Linux/WSL: crontab -e 加入  * 2 * * *  cd <项目根目录> && bash docker/backup.sh
#   - Windows:   任务计划程序，每天触发  bash docker/backup.sh
# 说明：容器内 pg_hba 对本地连接为 trust，pg_dump 无需密码；
#       备份文件为 gzip 压缩的 SQL，可用 docker compose exec -T db psql -U postgres -d aichat < 备份文件 恢复。
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"
TS="$(date +%Y%m%d_%H%M%S)"
FILE="aichat_pg_${TS}.sql.gz"

mkdir -p "$BACKUP_DIR"

# 仅当 db 服务在运行时才备份，避免误报
if ! docker compose ps db >/dev/null 2>&1 || [ -z "$(docker compose ps -q db 2>/dev/null)" ]; then
    echo "db 服务未运行，跳过备份" >&2
    exit 1
fi

docker compose exec -T db pg_dump -U postgres -d aichat | gzip > "${BACKUP_DIR}/${FILE}"
find "$BACKUP_DIR" -name 'aichat_pg_*.sql.gz' -mtime "+${KEEP_DAYS}" -delete
echo "backup ok: ${BACKUP_DIR}/${FILE}"
