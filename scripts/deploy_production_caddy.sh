#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# 生产部署脚本（Caddy + Streamlit，适配 Open Hands 自动执行）
# 文件：scripts/deploy_production_caddy.sh
# ============================================================
# 目标环境：
# - Alibaba Cloud Linux 3.2104（RHEL/CentOS 8 系）
# - Python 3.14.x
# - Caddy 已安装并占用 80 端口
# - 当前项目 Streamlit 使用 127.0.0.1:8504
#
# 设计原则：
# - 幂等：可重复执行，不会越跑越乱
# - 可观测：关键步骤统一输出 ✅ / ❌
# - 安全：默认通过 Caddy basicauth 做访问保护（Caddy 2 指令名无下划线，勿写成 basic_auth）
# - 可用性：crontab 每 5 分钟执行 scripts/watchdog_streamlit.sh，本机探测进程与 HTTP，挂了自动拉起
#
# Python 3.14 注意事项：
# - 明确使用 python3.14 创建虚拟环境
# - 不依赖系统默认 python3，避免版本漂移

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3.14}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8504}"
CADDY_CONFIG_DIR="${CADDY_CONFIG_DIR:-/etc/caddy}"
CADDY_MAIN_FILE="$CADDY_CONFIG_DIR/Caddyfile"
LOG_DIR="$PROJECT_DIR/logs"
STREAMLIT_LOG="$LOG_DIR/streamlit_8504.log"
DAILY_LOG="$LOG_DIR/daily_sync.log"
SERVICE_TAG="# index-monitor-quant919"

# 基础认证用户名和明文密码可通过环境变量传入（建议生产改强密码）
BASIC_AUTH_USER="${BASIC_AUTH_USER:-quantadmin}"
BASIC_AUTH_PASSWORD="${BASIC_AUTH_PASSWORD:-ChangeMe_123456!}"

ok() { echo "✅ $1"; }
fail() { echo "❌ $1"; exit 1; }
info() { echo "ℹ️  $1"; }

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || fail "缺少命令：$cmd"
}

append_caddy_block_if_missing() {
  local hashed_password="$1"
  local tmp_block
  tmp_block="$(mktemp)"

  cat > "$tmp_block" <<EOF

${SERVICE_TAG} BEGIN
www.quant919.com {
    encode zstd gzip
    basicauth {
        ${BASIC_AUTH_USER} ${hashed_password}
    }
    reverse_proxy 127.0.0.1:${STREAMLIT_PORT}
    log {
        output file /var/log/caddy/quant919_access.log
        format console
    }
}
${SERVICE_TAG} END
EOF

  if rg -q "${SERVICE_TAG} BEGIN" "$CADDY_MAIN_FILE"; then
    ok "检测到已有 quant919 Caddy 配置块（跳过追加）"
  else
    cat "$tmp_block" >> "$CADDY_MAIN_FILE"
    ok "已追加 quant919 Caddy 配置块"
  fi

  rm -f "$tmp_block"
}

setup_crontab() {
  local cron_tmp
  cron_tmp="$(mktemp)"

  # 保留现有 crontab，先移除本项目旧规则，再写入新规则
  crontab -l 2>/dev/null | rg -v "scheduler.daily_sync|index-monitor daily sync|watchdog_streamlit.sh|index-monitor streamlit watchdog" > "$cron_tmp" || true

  # 每天 08:15（UTC+8）执行增量同步
  echo "15 8 * * * cd \"$PROJECT_DIR\" && \"$VENV_DIR/bin/python\" -m scheduler.daily_sync >> \"$DAILY_LOG\" 2>&1 # index-monitor daily sync" >> "$cron_tmp"

  # 每 5 分钟：本机检查 Streamlit 进程 + 127.0.0.1:端口 HTTP，不健康则按生产参数重启
  echo "*/5 * * * * \"$PROJECT_DIR/scripts/watchdog_streamlit.sh\" # index-monitor streamlit watchdog" >> "$cron_tmp"

  crontab "$cron_tmp"
  rm -f "$cron_tmp"

  ok "crontab 已配置：每日 08:15 同步 + 每 5 分钟 Streamlit 健康守护"
}

start_streamlit() {
  mkdir -p "$LOG_DIR"

  # 幂等处理：先停掉旧的 8504 进程，再拉起新的
  pkill -f "streamlit run app/ui/main.py --server.port ${STREAMLIT_PORT}" >/dev/null 2>&1 || true
  pkill -f "streamlit run app/ui/main.py" >/dev/null 2>&1 || true

  nohup "$VENV_DIR/bin/streamlit" run app/ui/main.py \
    --server.address 127.0.0.1 \
    --server.port "$STREAMLIT_PORT" \
    --server.headless true \
    >> "$STREAMLIT_LOG" 2>&1 &

  sleep 2
  if rg -q "Local URL|Network URL|You can now view your Streamlit app" "$STREAMLIT_LOG"; then
    ok "Streamlit 已启动（127.0.0.1:${STREAMLIT_PORT}）"
  else
    info "未在日志中匹配到启动关键字，继续检查进程..."
    pgrep -f "streamlit run app/ui/main.py" >/dev/null 2>&1 || fail "Streamlit 启动失败，请查看日志：$STREAMLIT_LOG"
    ok "Streamlit 进程存在（请结合日志确认）"
  fi
}

main() {
  cd "$PROJECT_DIR"
  ok "开始生产部署（Caddy + Streamlit 8504）"

  require_cmd "$PYTHON_BIN"
  require_cmd caddy
  require_cmd crontab
  require_cmd rg

  [ -f "$CADDY_MAIN_FILE" ] || fail "未找到 Caddy 主配置文件：$CADDY_MAIN_FILE"

  # 1) 创建 venv + 安装依赖
  if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR" || fail "创建虚拟环境失败（python3.14）"
    ok "虚拟环境创建成功：$VENV_DIR"
  else
    ok "虚拟环境已存在：$VENV_DIR"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install --upgrade pip >/dev/null 2>&1 || true
  pip install -r requirements.txt || fail "安装依赖失败"
  ok "依赖安装完成"

  # 2) 配置 Caddy（basicauth + reverse_proxy）
  local_hash="$(caddy hash-password --plaintext "$BASIC_AUTH_PASSWORD" | tr -d '\n')"
  [ -n "$local_hash" ] || fail "生成 Caddy 密码哈希失败"
  ok "basicauth 密码哈希生成完成"

  append_caddy_block_if_missing "$local_hash"

  caddy validate --config "$CADDY_MAIN_FILE" || fail "Caddy 配置校验失败"
  ok "Caddy 配置校验通过"

  caddy reload --config "$CADDY_MAIN_FILE" || fail "Caddy reload 失败"
  ok "Caddy reload 成功"

  # 3) 配置定时任务（每日同步 + Streamlit 守护）
  chmod +x "$PROJECT_DIR/scripts/watchdog_streamlit.sh" 2>/dev/null || true
  setup_crontab

  # 4) 启动 Streamlit（仅本机回环，外部通过 Caddy 访问）
  start_streamlit

  echo
  ok "生产部署完成"
  echo "访问地址：https://www.quant919.com"
  echo "登录用户：${BASIC_AUTH_USER}"
  echo "登录密码：${BASIC_AUTH_PASSWORD}"
  echo
  echo "日志查看建议："
  echo "  - Streamlit 日志：tail -f ${STREAMLIT_LOG}"
  echo "  - Daily Sync 日志：tail -f ${DAILY_LOG}"
  echo "  - Caddy 日志：tail -f /var/log/caddy/quant919_access.log"
  echo
  echo "阿里云安全组建议开放端口：80、443、8504"
  echo "说明：8504 实际仅监听 127.0.0.1，外网访问走 443（Caddy 反代）"
}

main "$@"
