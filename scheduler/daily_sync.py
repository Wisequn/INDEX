"""
每日增量同步脚本（scheduler/daily_sync.py）。

用途：
- 每天定时执行一次，按固定顺序做“增量同步 + 指标重算”。
- 某一步失败不会中断全流程，后续步骤继续执行。

命令行：
- `python -m scheduler.daily_sync`：正常全流程
- `python -m scheduler.daily_sync --test`：快速自检（少跑几步 + 打印最近样本）
"""

from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any, Callable

from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.db.models import BtcFearGreed, BtcRsiPercentile
from btc.fetcher_ahr999 import fetch_full_history as fetch_ahr999_full
from btc.fetcher_ahr999 import fetch_incremental as fetch_ahr999_incremental
from btc.fetcher_fear_greed import fetch_incremental as fetch_fng_incremental
from btc.fetcher_ma import run_ma_pipeline
from btc.fetcher_price import fetch_incremental as fetch_price_incremental
from btc.indicators_rsi import run_rsi_pipeline


def _now_str() -> str:
    """
    返回当前时间字符串，格式：YYYY-MM-DD HH:MM:SS
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    """
    统一日志打印格式。
    """
    print(f"[{_now_str()}] {message}")


def _run_step(step_name: str, success_text: str, func: Callable[[], Any]) -> dict[str, Any]:
    """
    执行一个步骤，并记录耗时与状态。

    参数：
    - step_name: 步骤内部名称（用于统计）
    - success_text: 成功时要打印的中文文案
    - func: 具体执行函数
    """
    start = perf_counter()
    try:
        result = func()
        elapsed = perf_counter() - start
        _log(f"✅ {success_text}")
        return {"step": step_name, "ok": True, "elapsed_sec": elapsed, "result": result, "error": None}
    except Exception as exc:
        elapsed = perf_counter() - start
        _log(f"❌ {success_text.replace('完成', '失败')}：{exc}")
        return {"step": step_name, "ok": False, "elapsed_sec": elapsed, "result": None, "error": str(exc)}


def _print_test_sample_summary() -> None:
    """
    TEST 模式尾部：打印最近若干行的多窗口百分位，便于肉眼核对是否入库。
    """
    with SessionLocal() as session:
        rsi_rows = session.execute(select(BtcRsiPercentile).order_by(BtcRsiPercentile.date.desc()).limit(10)).scalars().all()
        fg_rows = session.execute(select(BtcFearGreed).order_by(BtcFearGreed.date.desc()).limit(5)).scalars().all()

    _log("--- 🧪 TEST 样本：最近 10 日 btc_rsi_percentile（RSI6 多窗口）---")
    for r in reversed(rsi_rows):
        _log(
            f"{r.date} | rsi6_pct 1y={r.rsi6_pct_1y} 2y={r.rsi6_pct_2y} 3y={r.rsi6_pct_3y} 4y={r.rsi6_pct_4y} "
            f"| rsi12_pct 1y={r.rsi12_pct_1y} 2y={r.rsi12_pct_2y} 3y={r.rsi12_pct_3y} 4y={r.rsi12_pct_4y}"
        )

    _log("--- 🧪 TEST 样本：最近 5 日 btc_fear_greed（多窗口）---")
    for r in reversed(fg_rows):
        _log(
            f"{r.date} | value={r.value} | fg_pct 1y={r.fg_pct_1y} 2y={r.fg_pct_2y} 3y={r.fg_pct_3y} 4y={r.fg_pct_4y}"
        )


def run_daily_sync(test: bool = False) -> list[dict[str, Any]]:
    """
    按顺序执行每日同步流程：
    1) BTC 价格：先补「中间断层」，再追「尾部新数据」
    2) RSI 重算（全表基于 btc_price 重算并 upsert，天然覆盖补数后的日期）
    3) 恐惧贪婪（全历史重算百分位后整表 upsert）
    4) Ahr999：若第 1 步补过中间价格断层，则本步改为「全量重写入」；否则仍走增量
       （原因：价格补洞后 Ahr999 序列需与价格重新对齐）
    5) 4年均线/200周均线重算（同样全表重算）

    参数：
    - test: True 时跳过 Ahr999 与均线，加快本地验证；末尾打印 RSI/F&G 多窗口样本
    """
    # 幂等补列 + 建表（含 schema_migrations）
    init_db()

    if test:
        _log("🧪 TEST 模式开启：将跳过 Ahr999 / 均线，仅验证价格→RSI→恐惧贪婪 与多窗口百分位入库")

    results: list[dict[str, Any]] = []

    results.append(_run_step("price", "BTC价格同步完成", fetch_price_incremental))

    # 从价格步骤结果里读取「是否刚修补了中间断层」，决定 Ahr999 用增量还是全量
    price_result = results[-1].get("result")
    force_ahr999_full = bool(
        isinstance(price_result, dict) and price_result.get("had_internal_gaps_filled") is True
    )
    if force_ahr999_full and not test:
        _log("ℹ️ 检测到本次修补了 btc_price 中间断层，Ahr999 将执行全量重写入以保证与价格对齐。")

    results.append(_run_step("rsi", "RSI指标计算完成", run_rsi_pipeline))
    results.append(_run_step("fear_greed", "恐惧贪婪指数同步完成", fetch_fng_incremental))

    if not test:
        ahr999_job: Callable[[], Any] = fetch_ahr999_full if force_ahr999_full else fetch_ahr999_incremental
        results.append(_run_step("ahr999", "Ahr999指标同步完成", ahr999_job))
        results.append(_run_step("ma", "均线计算完成", run_ma_pipeline))
    else:
        _log("⏭️ TEST：已跳过 Ahr999 / 均线步骤")

    if test:
        _print_test_sample_summary()

    _log("🎉 今日同步全部完成！")
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Index Monitor 每日同步")
    parser.add_argument(
        "--test",
        action="store_true",
        help="快速自检：跳过 Ahr999/均线，并打印最近 RSI/F&G 多窗口百分位样本",
    )
    args = parser.parse_args()
    run_daily_sync(test=args.test)
