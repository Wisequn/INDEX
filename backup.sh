#!/usr/bin/env bash
set -euo pipefail

# =========================
# SQLite 数据库备份脚本
# =========================
# 用法：
#   bash backup.sh

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="$PROJECT_DIR/index_monitor.db"
BACKUP_DIR="$PROJECT_DIR/backups"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
  echo "数据库不存在：$DB_PATH"
  exit 1
fi

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/index_monitor_${TS}.db"

# 先复制一份快照
cp "$DB_PATH" "$BACKUP_FILE"

# 再用 sqlite3 integrity_check 做快速校验（如果系统装了 sqlite3）
if command -v sqlite3 >/dev/null 2>&1; then
  CHECK_RESULT="$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;" || true)"
  if [ "$CHECK_RESULT" != "ok" ]; then
    echo "备份校验失败：$BACKUP_FILE"
    exit 1
  fi
fi

# 清理过期备份
find "$BACKUP_DIR" -type f -name "index_monitor_*.db" -mtime +"$RETENTION_DAYS" -delete

echo "备份完成：$BACKUP_FILE"
