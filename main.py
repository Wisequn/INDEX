"""
项目首次全量初始化入口（main.py）。

用途：
- 一键按顺序全量抓取/计算所有数据
- 适合第一次部署时运行

与 scheduler/daily_sync.py 的区别：
- main.py：全量初始化
- daily_sync.py：每日增量更新
"""

from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any, Callable

from app.db.database import init_db
from btc.fetcher_ahr999 import fetch_full_history as fetch_ahr999_full
from btc.fetcher_fear_greed import fetch_full_history as fetch_fng_full
from btc.fetcher_ma import run_ma_pipeline
from btc.fetcher_price import fetch_full_history as fetch_price_full
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
    执行一个全量步骤，记录结果与耗时。
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


def run_full_initialization() -> list[dict[str, Any]]:
    """
    一键全量初始化顺序：
    1) BTC 价格全量
    2) RSI 全量计算
    3) 恐惧贪婪全量
    4) Ahr999 全量
    5) 均线重算
    """
    # 建表 + 幂等补列（含多窗口百分位列）
    init_db()

    results: list[dict[str, Any]] = []

    results.append(_run_step("price_full", "BTC价格全量同步完成", fetch_price_full))
    results.append(_run_step("rsi_full", "RSI指标全量计算完成", run_rsi_pipeline))
    results.append(_run_step("fear_greed_full", "恐惧贪婪指数全量同步完成", fetch_fng_full))
    results.append(_run_step("ahr999_full", "Ahr999指标全量同步完成", fetch_ahr999_full))
    results.append(_run_step("ma_full", "均线全量计算完成", run_ma_pipeline))

    _log("🎉 首次全量初始化全部完成！")
    return results


if __name__ == "__main__":
    run_full_initialization()
