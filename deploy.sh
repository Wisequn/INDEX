#!/usr/bin/env bash
set -euo pipefail

# =========================
# Index Monitor 一键部署脚本
# =========================
# 用法：
# 1) 本地/普通服务器部署（原逻辑）：
#    bash deploy.sh
# 2) 生产 Caddy 部署（新增）：
#    bash deploy.sh production

MODE="${1:-default}"

# 生产模式：直接转发到 scripts/deploy_production_caddy.sh
if [ "$MODE" = "production" ]; then
  echo "[INFO] 进入生产部署模式（Caddy + 8504）"
  bash "$(cd "$(dirname "$0")" && pwd)/scripts/deploy_production_caddy.sh"
  exit 0
fi

# 功能（default 模式）：
# 1) 创建虚拟环境并安装依赖
# 2) 首次全量初始化数据
# 3) 配置定时任务（08:15 同步 + 每5分钟实时值/告警 + 09:00 早报 + 08:40 备份）
# 4) 后台启动 Streamlit

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"

cd "$PROJECT_DIR"

echo "[1/4] 创建虚拟环境并安装依赖..."
if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "[2/4] 执行首次全量初始化（main.py）..."
python main.py

echo "[3/4] 配置 crontab..."
CRON_TMP="$(mktemp)"
crontab -l 2>/dev/null | rg -v "scheduler.daily_sync|scheduler.realtime_update|scheduler.daily_morning_report|scheduler.alert_engine --daily-briefing|backup.sh" > "$CRON_TMP" || true

# 每天 08:15 执行增量同步（UTC+8）
echo "15 8 * * * cd \"$PROJECT_DIR\" && \"$VENV_DIR/bin/python\" -m scheduler.daily_sync >> \"$PROJECT_DIR/logs/daily_sync.log\" 2>&1" >> "$CRON_TMP"
# 每天 08:40 执行数据库备份
echo "40 8 * * * cd \"$PROJECT_DIR\" && /usr/bin/env bash \"$PROJECT_DIR/backup.sh\" >> \"$PROJECT_DIR/logs/backup.log\" 2>&1" >> "$CRON_TMP"
chmod +x "$PROJECT_DIR/scripts/run_with_env.sh"
# 每 5 分钟更新 realtime_values + 实时告警（自动读取 .env 里的 Telegram 配置）
echo "*/5 * * * * \"$PROJECT_DIR/scripts/run_with_env.sh\" \"$VENV_DIR/bin/python\" -m scheduler.realtime_update >> \"$PROJECT_DIR/logs/realtime_update.log\" 2>&1" >> "$CRON_TMP"
# 每天 09:00 每日早报（自动读取 .env）
echo "0 9 * * * \"$PROJECT_DIR/scripts/run_with_env.sh\" \"$VENV_DIR/bin/python\" -m scheduler.daily_morning_report >> \"$PROJECT_DIR/logs/daily_morning_report.log\" 2>&1" >> "$CRON_TMP"

crontab "$CRON_TMP"
rm -f "$CRON_TMP"

mkdir -p "$PROJECT_DIR/logs"

echo "[4/4] 后台启动 Streamlit..."
pkill -f "streamlit run app/ui/main.py" || true
nohup "$VENV_DIR/bin/streamlit" run app/ui/main.py --server.address 0.0.0.0 --server.port "$STREAMLIT_PORT" >> "$PROJECT_DIR/logs/streamlit.log" 2>&1 &

echo "部署完成。"
echo "浏览器访问地址: http://<你的服务器IP>:${STREAMLIT_PORT}"
