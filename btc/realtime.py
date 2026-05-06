"""
实时价格 + 实时指标模块（btc/realtime.py）。

功能目标：
1) 每 5 分钟从币安获取一次 BTC 实时价格
2) 用「实时价格 + 数据库历史数据」计算当前指标值
3) 缓存在内存中，供 Streamlit 页面快速读取

说明：
- 本模块不写数据库
- percentiles_eod：根据库中全历史序列，对「最近一个有效 RSI/F&G/Ahr999 点」
  用与前端一致的滚动窗口动态算出多窗口百分位（不入库）
"""

from __future__ import annotations

import json
import ssl
import threading
import time
from datetime import datetime
from typing import Any
from urllib.request import urlopen

import pandas as pd
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Btc200wMa, Btc4yMa, BtcAhr999, BtcFearGreed, BtcPrice, BtcRsi, RealtimeValue
from btc.indicators_rsi import compute_wilder_rsi
from btc.percentile_runtime import build_eod_percentile_api_dict

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

CACHE_TTL_SECONDS = 5 * 60
REFRESH_INTERVAL_SECONDS = 5 * 60

_cache_data: dict[str, Any] | None = None
_cache_timestamp: float = 0.0
_cache_lock = threading.Lock()
_refresh_thread_started = False


def _fetch_realtime_price() -> float:
    """从币安读取当前 BTCUSDT 实时价格（含重试）。"""
    max_retries = 3
    ssl_context = ssl.create_default_context()
    insecure_ssl_context = ssl._create_unverified_context()

    for attempt in range(1, max_retries + 1):
        try:
            with urlopen(BINANCE_TICKER_URL, timeout=20, context=ssl_context) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP 状态码异常: {resp.status}")
                payload = json.loads(resp.read().decode("utf-8"))
                return float(payload["price"])
        except Exception as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                try:
                    with urlopen(BINANCE_TICKER_URL, timeout=20, context=insecure_ssl_context) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP 状态码异常: {resp.status}")
                        payload = json.loads(resp.read().decode("utf-8"))
                        return float(payload["price"])
                except Exception as inner_exc:
                    if attempt == max_retries:
                        raise RuntimeError(f"获取实时价格失败，已重试 {max_retries} 次: {inner_exc}") from inner_exc
                    time.sleep(5)
                    continue

            if attempt == max_retries:
                raise RuntimeError(f"获取实时价格失败，已重试 {max_retries} 次: {exc}") from exc
            time.sleep(5)

    raise RuntimeError("获取实时价格失败：未知错误")


def _compute_realtime_metrics(price_now: float) -> dict[str, Any]:
    """
    用实时价格计算当前指标值；EOD 百分位从库中全历史序列动态计算。
    """
    with SessionLocal() as session:
        close_rows = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.desc()).limit(200)).all()
        close_rows = list(reversed(close_rows))
        close_series = pd.Series([float(r.close) for r in close_rows], dtype="float64")

        close_with_now = pd.concat([close_series, pd.Series([price_now], dtype="float64")], ignore_index=True)
        rsi6_now = float(compute_wilder_rsi(close_with_now, period=6).iloc[-1])
        rsi12_now = float(compute_wilder_rsi(close_with_now, period=12).iloc[-1])

        latest_4y = session.execute(select(Btc4yMa).order_by(Btc4yMa.date.desc()).limit(1)).scalar_one_or_none()
        latest_200w = session.execute(select(Btc200wMa).order_by(Btc200wMa.date.desc()).limit(1)).scalar_one_or_none()

        price_to_4y_ma = None
        if latest_4y is not None and latest_4y.ma_value:
            price_to_4y_ma = float(price_now / float(latest_4y.ma_value))

        price_to_200w_ma = None
        if latest_200w is not None and latest_200w.ma_value:
            price_to_200w_ma = float(price_now / float(latest_200w.ma_value))

        latest_ahr = session.execute(select(BtcAhr999).order_by(BtcAhr999.date.desc()).limit(1)).scalar_one_or_none()
        latest_price_row = session.execute(select(BtcPrice).order_by(BtcPrice.date.desc()).limit(1)).scalar_one_or_none()

        ahr_now = None
        if latest_ahr is not None and latest_price_row is not None and latest_price_row.close:
            ahr_now = float(latest_ahr.ahr999_value) * float(price_now) / float(latest_price_row.close)

        rsi_hist = session.execute(select(BtcRsi.rsi6, BtcRsi.rsi12).order_by(BtcRsi.date.asc())).all()
        rsi6_hist = pd.Series([r.rsi6 for r in rsi_hist], dtype="float64")
        rsi12_hist = pd.Series([r.rsi12 for r in rsi_hist], dtype="float64")

        fg_hist = session.execute(select(BtcFearGreed.value).order_by(BtcFearGreed.date.asc())).all()
        fg_series = pd.Series([r.value for r in fg_hist], dtype="float64")

        ahr_hist = session.execute(select(BtcAhr999.ahr999_value).order_by(BtcAhr999.date.asc())).all()
        ahr_series = pd.Series([r.ahr999_value for r in ahr_hist], dtype="float64")

        latest_price_date = latest_price_row.date if latest_price_row else None

    pct_block: dict[str, Any] = {"pct_asof_date": latest_price_date}
    pct_block.update(build_eod_percentile_api_dict(rsi6_hist, "rsi6"))
    pct_block.update(build_eod_percentile_api_dict(rsi12_hist, "rsi12"))
    pct_block.update(build_eod_percentile_api_dict(fg_series, "fg"))
    pct_block.update(build_eod_percentile_api_dict(ahr_series, "ahr999"))

    return {
        "price": float(price_now),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rsi6": rsi6_now,
        "rsi12": rsi12_now,
        "price_to_4y_ma": price_to_4y_ma,
        "price_to_200w_ma": price_to_200w_ma,
        "ahr999": ahr_now,
        "percentiles_eod": pct_block,
    }


def _refresh_cache_once() -> dict[str, Any]:
    global _cache_data, _cache_timestamp

    try:
        price_now = _fetch_realtime_price()
        fresh_data = _compute_realtime_metrics(price_now)
        with _cache_lock:
            _cache_data = fresh_data
            _cache_timestamp = time.time()
        return fresh_data
    except Exception:
        with _cache_lock:
            if _cache_data is not None:
                return dict(_cache_data)
        raise


def get_realtime_data() -> dict[str, Any]:
    with _cache_lock:
        cache_valid = _cache_data is not None and (time.time() - _cache_timestamp) < CACHE_TTL_SECONDS
        if cache_valid:
            return dict(_cache_data)

    return _refresh_cache_once()


def get_realtime_values(window: str = "2Y") -> dict[str, dict[str, Any]]:
    """
    从 realtime_values 表读取最新值，返回：
    {indicator_code: {"current_value": x, "update_time": "...", "window": "2Y"}}
    """
    from app.db.database import init_db

    init_db()
    with SessionLocal() as session:
        rows = session.execute(
            select(RealtimeValue)
            .where(RealtimeValue.window == window)
            .order_by(RealtimeValue.update_time.desc())
        ).scalars().all()

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        # 同一个 indicator_code 可能有历史记录，取最新 update_time 的一条
        if r.indicator_code in out:
            continue
        out[str(r.indicator_code)] = {
            "current_value": r.current_value,
            "update_time": r.update_time,
            "window": r.window,
        }
    return out


def _background_loop() -> None:
    while True:
        try:
            _refresh_cache_once()
        except Exception:
            pass
        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_background_refresh() -> None:
    global _refresh_thread_started

    with _cache_lock:
        if _refresh_thread_started:
            return
        _refresh_thread_started = True

    t = threading.Thread(target=_background_loop, daemon=True, name="btc-realtime-refresh")
    t.start()


if __name__ == "__main__":
    start_background_refresh()
    print(get_realtime_data())
