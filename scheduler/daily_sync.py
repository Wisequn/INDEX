"""
每日增量同步脚本（scheduler/daily_sync.py）。

用途：
- 每天定时执行一次，按固定顺序做“增量同步 + 指标重算”。
- 某一步失败不会中断全流程，后续步骤继续执行。
"""

from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any, Callable

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


def run_daily_sync() -> list[dict[str, Any]]:
    """
    按顺序执行每日同步流程：
    1) BTC 价格增量
    2) RSI 重算
    3) 恐惧贪婪增量
    4) Ahr999 增量
    5) 4年均线/200周均线重算
    """
    results: list[dict[str, Any]] = []

    results.append(_run_step("price", "BTC价格同步完成", fetch_price_incremental))
    results.append(_run_step("rsi", "RSI指标计算完成", run_rsi_pipeline))
    results.append(_run_step("fear_greed", "恐惧贪婪指数同步完成", fetch_fng_incremental))
    results.append(_run_step("ahr999", "Ahr999指标同步完成", fetch_ahr999_incremental))
    results.append(_run_step("ma", "均线计算完成", run_ma_pipeline))

    _log("🎉 今日同步全部完成！")
    return results


if __name__ == "__main__":
    run_daily_sync()
