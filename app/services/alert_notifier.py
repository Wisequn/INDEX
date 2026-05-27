"""
告警消息推送：Telegram / 通用 Webhook / 本地日志。

环境变量（任选其一或组合）：
- TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
- ALERT_WEBHOOK_URL（POST JSON: {"text": "..."} 或 {"content": "..."}）
- 均未配置时写入 logs/alerts_push.log 并打印到 stdout（便于联调）
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALERT_LOG = _PROJECT_ROOT / "logs" / "alerts_push.log"


def _log_local(message: str) -> None:
    _ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with open(_ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    print(line.rstrip())


def _ssl_context() -> ssl.SSLContext:
    """macOS 自带 Python 常缺根证书；优先用 certifi 的 CA 包。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post_json(url: str, payload: dict[str, object], timeout: int = 15) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}")


def send_telegram_message(text: str, *, timeout: int = 15) -> bool:
    """
    通过 Telegram Bot API 发送纯文本消息。

    需配置环境变量：TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID。
    成功返回 True；未配置或发送失败返回 False。
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    tg_url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        _post_json(tg_url, {"chat_id": chat_id, "text": text}, timeout=timeout)
        return True
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        _log_local(f"Telegram 发送失败: {exc}")
        return False


def send_daily_report_message(text: str, *, dry_run: bool = False) -> bool:
    """发送每日早报（Telegram 优先，其次 Webhook，否则本地日志）。"""
    return send_alert_message(text, dry_run=dry_run)


def send_alert_message(text: str, *, dry_run: bool = False) -> bool:
    """
    发送一条告警/早报文本。返回是否至少成功走了一种外发渠道。
    dry_run=True 时只写本地日志。
    """
    if dry_run:
        _log_local(f"[dry-run] {text}")
        return True

    sent = send_telegram_message(text)
    webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()

    if webhook:
        try:
            _post_json(webhook, {"text": text, "content": text})
            sent = True
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            _log_local(f"Webhook 发送失败: {exc}")

    if not sent:
        _log_local(text)

    return sent
