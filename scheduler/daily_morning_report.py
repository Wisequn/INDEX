"""
Index 每日早报（建议每天 09:00 由 cron 执行）。

流程：
1. 读取库内最新交易日指标
2. calculate_total_score() 计算抄底分数
3. 写入 btc_bottom_score
4. 按模板发送早报（Telegram / Webhook / 本地日志）

用法（仓库根目录）：
    python -m scheduler.daily_morning_report
    python -m scheduler.daily_morning_report --dry-run

Crontab（Asia/Shanghai，与 deploy.sh 一致）：
    0 9 * * * cd /opt/INDEX && /opt/INDEX/.venv/bin/python -m scheduler.daily_morning_report >> /opt/INDEX/logs/daily_morning_report.log 2>&1
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.db.models import Btc200wMa, BtcPrice
from app.services.alert_notifier import send_daily_report_message
from scheduler.alert_engine import (
    build_inputs_for_date,
    calculate_normalized_score_and_position,
    calculate_total_score,
    persist_bottom_score_for_date,
)
from scheduler.alert_messages import BriefingSnapshot, format_daily_briefing


def run_daily_morning_report(*, dry_run: bool = False) -> str:
    """
    生成并发送每日早报；将最新交易日分数写入 btc_bottom_score。
    返回已格式化的早报正文。
    """
    init_db()
    report_date = datetime.now().strftime("%Y-%m-%d")

    with SessionLocal() as session:
        latest_date = session.execute(
            select(BtcPrice.date).order_by(BtcPrice.date.desc()).limit(1)
        ).scalar_one()
        data_date = str(latest_date)

        inputs = build_inputs_for_date(session, data_date)
        if inputs is None:
            raise ValueError("无法生成早报：btc_price 无数据")

        ma200 = session.execute(
            select(Btc200wMa).where(Btc200wMa.date == data_date)
        ).scalar_one_or_none()
        price_to_200w = (
            float(ma200.price_to_200w_ma)
            if ma200 is not None and ma200.price_to_200w_ma is not None
            else None
        )

    score = calculate_total_score(inputs=inputs)
    persist_bottom_score_for_date(data_date, score=score)
    score_meta = calculate_normalized_score_and_position(raw_score=score)

    brief = BriefingSnapshot(
        close=inputs.close,
        close_2y_low=inputs.close_2y_low,
        rsi6=inputs.rsi6,
        rsi6_pct_ui=inputs.rsi6_pct_ui,
        rsi12=inputs.rsi12,
        rsi12_pct_ui=inputs.rsi12_pct_ui,
        value=inputs.value,
        fg_pct_ui=inputs.fg_pct_ui,
        ahr999_value=inputs.ahr999_value,
        ahr999_pct_ui=inputs.ahr999_pct_ui,
        price_to_4y_ma=inputs.price_to_4y_ma,
        price_to_200w_ma=price_to_200w,
        report_date=report_date,
    )
    text = format_daily_briefing(brief, score_meta=score_meta)
    send_daily_report_message(text, dry_run=dry_run)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Index 每日早报")
    parser.add_argument("--dry-run", action="store_true", help="仅写本地日志，不调用 Telegram/Webhook")
    args = parser.parse_args()

    text = run_daily_morning_report(dry_run=args.dry_run)
    print(f"[daily_morning_report] data persisted, score in btc_bottom_score")
    print(text)


if __name__ == "__main__":
    main()
