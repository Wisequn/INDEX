#!/usr/bin/env bash
# ============================================================
# Streamlit 健康检查 + 必要时自动拉起（供 crontab 每 5 分钟调用）
# ============================================================
# 用法：由 deploy_production_caddy.sh 写入 crontab，无需手工执行。
#
# 逻辑（大白话）：
# 1) 用文件锁避免两次检查叠在一起重复重启
# 2) 看进程在不在 + 本机 HTTP 能不能打开首页
# 3) 不健康就：先停旧进程，再按与生产部署相同参数 nohup 拉起
# 4) 每次动作用一行写进 logs/watchdog_streamlit.log，方便你 tail 验收
#
# 环境变量（可选，与 deploy 脚本一致）：
# - STREAMLIT_PORT：默认 8504

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
STREAMLIT_BIN="$VENV_DIR/bin/streamlit"
STREAMLIT_PORT="${STREAMLIT_PORT:-8504}"
STREAMLIT_LOG="$PROJECT_DIR/logs/streamlit_8504.log"
WATCHDOG_LOG="$PROJECT_DIR/logs/watchdog_streamlit.log"

log_line() {
  mkdir -p "$(dirname "$WATCHDOG_LOG")"
  echo "[$(date "+%Y-%m-%d %H:%M:%S")] $*" >>"$WATCHDOG_LOG"
}

# 单实例：若上一次检查/重启尚未结束，本次直接退出
if command -v flock >/dev/null 2>&1; then
  exec 200>/tmp/index-monitor-streamlit-watchdog.lock
  flock -n 200 || exit 0
fi

streamlit_responds() {
  local code="000"
  # 注意：在 set -e 下，curl 失败不能直接赋值否则脚本会退出，因此用 || 兜底
  code="$(
    curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 6 "http://127.0.0.1:${STREAMLIT_PORT}/" 2>/dev/null
  )" || code="000"
  [[ "$code" == "200" || "$code" == "301" || "$code" == "302" ]]
}

streamlit_process_ok() {
  pgrep -f "streamlit run app/ui/main.py" >/dev/null 2>&1
}

restart_streamlit() {
  log_line "开始重启：先结束旧 streamlit 进程"
  pkill -f "streamlit run app/ui/main.py --server.port ${STREAMLIT_PORT}" >/dev/null 2>&1 || true
  pkill -f "streamlit run app/ui/main.py" >/dev/null 2>&1 || true
  sleep 2

  if [[ ! -x "$STREAMLIT_BIN" ]]; then
    log_line "❌ 未找到可执行文件：$STREAMLIT_BIN（跳过拉起，请先 deploy 创建 venv）"
    return 1
  fi

  mkdir -p "$(dirname "$STREAMLIT_LOG")"
  nohup "$STREAMLIT_BIN" run app/ui/main.py \
    --server.address 127.0.0.1 \
    --server.port "$STREAMLIT_PORT" \
    --server.headless true \
    >>"$STREAMLIT_LOG" 2>&1 &
  sleep 3

  if streamlit_process_ok && streamlit_responds; then
    log_line "✅ 重启后健康检查通过（端口 ${STREAMLIT_PORT}）"
    return 0
  fi
  log_line "❌ 重启后仍不健康，请人工查看：$STREAMLIT_LOG"
  return 1
}

main() {
  if streamlit_process_ok && streamlit_responds; then
    exit 0
  fi

  log_line "⚠️ 检测到异常（进程或 HTTP 不健康），准备重启…"
  restart_streamlit || exit 1
}

main "$@"
