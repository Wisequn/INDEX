"""
实时价格 + 实时指标模块（btc/realtime.py）。

功能目标：
1) 每 5 分钟从币安获取一次 BTC 实时价格
2) 用“实时价格 + 数据库历史数据”计算当前指标值
3) 缓存在内存中，供 Streamlit 页面快速读取

说明：
- 本模块不写数据库
- 只返回内存里的实时结果字典
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
from app.db.models import Btc200wMa, Btc4yMa, BtcAhr999, BtcPrice
from btc.indicators_rsi import compute_wilder_rsi

# 币安实时价格接口（无需 Key）
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

# 缓存有效期：5分钟
CACHE_TTL_SECONDS = 5 * 60

# 线程刷新间隔：5分钟
REFRESH_INTERVAL_SECONDS = 5 * 60

# 模块级缓存对象（只在内存存在）
_cache_data: dict[str, Any] | None = None
_cache_timestamp: float = 0.0
_cache_lock = threading.Lock()
_refresh_thread_started = False


def _fetch_realtime_price() -> float:
    """
    从币安读取当前 BTCUSDT 实时价格。
    含 3 次重试，每次失败等待 5 秒。
    """
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
            # 某些网络环境可能有证书链问题，自动回退
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
    用实时价格计算当前指标值。

    计算逻辑：
    - RSI6/RSI12：读取最近200天 close，再把实时价格拼到末尾，重算 RSI
    - price_to_4y_ma：实时价格 / 最新 4y ma_value
    - price_to_200w_ma：实时价格 / 最新 200w ma_value
    - ahr999：用“最新日 ahr999 与 最新收盘价”的比例外推实时值
      （即 ahr999_now = ahr999_latest * price_now / close_latest）
    """
    with SessionLocal() as session:
        # 1) 读取最近 200 天收盘价，用于实时 RSI
        close_rows = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.desc()).limit(200)).all()
        close_rows = list(reversed(close_rows))  # 转回升序
        close_series = pd.Series([float(r.close) for r in close_rows], dtype="float64")

        # 把实时价格当作“当前时刻最新一条”拼接到末尾
        close_with_now = pd.concat([close_series, pd.Series([price_now], dtype="float64")], ignore_index=True)
        rsi6_now = float(compute_wilder_rsi(close_with_now, period=6).iloc[-1])
        rsi12_now = float(compute_wilder_rsi(close_with_now, period=12).iloc[-1])

        # 2) 读取均线表最新值
        latest_4y = session.execute(select(Btc4yMa).order_by(Btc4yMa.date.desc()).limit(1)).scalar_one_or_none()
        latest_200w = session.execute(select(Btc200wMa).order_by(Btc200wMa.date.desc()).limit(1)).scalar_one_or_none()

        price_to_4y_ma = None
        if latest_4y is not None and latest_4y.ma_value:
            price_to_4y_ma = float(price_now / float(latest_4y.ma_value))

        price_to_200w_ma = None
        if latest_200w is not None and latest_200w.ma_value:
            price_to_200w_ma = float(price_now / float(latest_200w.ma_value))

        # 3) 读取最新 Ahr999，并用价格比例做实时近似更新
        latest_ahr = session.execute(select(BtcAhr999).order_by(BtcAhr999.date.desc()).limit(1)).scalar_one_or_none()
        latest_price_row = session.execute(select(BtcPrice).order_by(BtcPrice.date.desc()).limit(1)).scalar_one_or_none()

        ahr_now = None
        if latest_ahr is not None and latest_price_row is not None and latest_price_row.close:
            ahr_now = float(latest_ahr.ahr999_value) * float(price_now) / float(latest_price_row.close)

    return {
        "price": float(price_now),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rsi6": rsi6_now,
        "rsi12": rsi12_now,
        "price_to_4y_ma": price_to_4y_ma,
        "price_to_200w_ma": price_to_200w_ma,
        "ahr999": ahr_now,
    }


def _refresh_cache_once() -> dict[str, Any]:
    """
    执行一次缓存刷新：
    - 成功：更新缓存并返回新数据
    - 失败：返回旧缓存（如果有），并保留旧 updated_at
    """
    global _cache_data, _cache_timestamp

    try:
        price_now = _fetch_realtime_price()
        fresh_data = _compute_realtime_metrics(price_now)
        with _cache_lock:
            _cache_data = fresh_data
            _cache_timestamp = time.time()
        return fresh_data
    except Exception:
        # 网络失败等情况：返回上次成功缓存
        with _cache_lock:
            if _cache_data is not None:
                return dict(_cache_data)
        # 如果连历史缓存都没有，就抛出错误
        raise


def get_realtime_data() -> dict[str, Any]:
    """
    对外函数1：获取实时数据。

    规则：
    - 如果缓存还在 5 分钟有效期内，直接返回缓存
    - 否则主动刷新一次并返回
    """
    with _cache_lock:
        cache_valid = _cache_data is not None and (time.time() - _cache_timestamp) < CACHE_TTL_SECONDS
        if cache_valid:
            return dict(_cache_data)

    return _refresh_cache_once()


def _background_loop() -> None:
    """
    后台线程循环：
    - 启动后先刷新一次
    - 之后每 5 分钟刷新一次
    """
    while True:
        try:
            _refresh_cache_once()
        except Exception:
            # 后台线程不让异常中断，静默保活
            pass
        time.sleep(REFRESH_INTERVAL_SECONDS)


def start_background_refresh() -> None:
    """
    对外函数2：启动后台刷新线程（只会启动一次）。
    """
    global _refresh_thread_started

    with _cache_lock:
        if _refresh_thread_started:
            return
        _refresh_thread_started = True

    t = threading.Thread(target=_background_loop, daemon=True, name="btc-realtime-refresh")
    t.start()


if __name__ == "__main__":
    # 本地手动测试入口
    start_background_refresh()
    print(get_realtime_data())
