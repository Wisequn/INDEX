"""
RSI 指标计算脚本。

本脚本做 2 件事：
1) 从 btc_price 读取收盘价，按日期从早到晚排序
2) 使用标准 Wilder 平滑算法计算 RSI6 / RSI12，并写入 btc_rsi（upsert）

说明：
- 历史百分位不再入库；由 Streamlit 全历史内存表动态计算（app/ui/chart_history.py）
"""

from __future__ import annotations

import json
import ssl
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
from sqlalchemy import select

from app.db.database import SessionLocal, init_db, upsert_by_date
from app.db.models import BtcPrice, BtcRsi

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def load_price_history() -> pd.DataFrame:
    """
    从数据库读取 btc_price 的历史收盘价，并按日期升序排序。
    """
    try:
        binance_df = load_price_history_from_binance()
        if not binance_df.empty:
            return binance_df
    except Exception:
        # 网络抖动或证书问题时，回退到本地 btc_price，保证任务可继续运行
        pass

    with SessionLocal() as session:
        rows = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.asc())).all()
    if not rows:
        return pd.DataFrame(columns=["date", "close"])
    return pd.DataFrame(rows, columns=["date", "close"])


def load_price_history_from_binance(*, interval: str = "1d", chunks: int = 5) -> pd.DataFrame:
    """
    从 Binance Kline 拉取最近若干批历史收盘价。

    - 每批 1000 根 K 线，默认 chunks=5（最多约 5000 天，够 RSI 计算）
    - 优先用于 RSI，避免与交易所图表口径不一致
    """
    ssl_ctx = ssl.create_default_context()
    insecure_ssl_ctx = ssl._create_unverified_context()
    out: list[dict[str, float | str]] = []
    end_time_ms: int | None = None

    for _ in range(chunks):
        query = {"symbol": "BTCUSDT", "interval": interval, "limit": 1000}
        if end_time_ms is not None:
            query["endTime"] = end_time_ms
        url = f"{BINANCE_KLINES_URL}?{urlencode(query)}"
        try:
            with urlopen(url, timeout=20, context=ssl_ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                raise
            # 与 realtime ticker 保持一致：证书链异常时尝试不校验兜底
            with urlopen(url, timeout=20, context=insecure_ssl_ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        if not payload:
            break

        for row in payload:
            # row[0] 开盘时间（ms），用 UTC 日期作为索引
            dt = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).date().isoformat()
            out.append({"date": dt, "close": float(row[4])})

        first_open_ms = int(payload[0][0])
        end_time_ms = first_open_ms - 1
        if len(payload) < 1000:
            break

    if not out:
        return pd.DataFrame(columns=["date", "close"])

    df = pd.DataFrame(out).drop_duplicates(subset=["date"], keep="last")
    return df.sort_values("date").reset_index(drop=True)


def compute_wilder_rsi(close_series: pd.Series, period: int) -> pd.Series:
    """
    使用标准 Wilder 平滑算法计算 RSI。

    Wilder 思路（大白话）：
    - 先算每天涨跌幅（delta）
    - 把涨幅和跌幅分开
    - 前 period 天用“简单平均”做初始 avg_gain / avg_loss
    - 从下一天开始用 Wilder 平滑递推：
      avg_gain = (前一天avg_gain*(period-1) + 当天gain) / period
      avg_loss = (前一天avg_loss*(period-1) + 当天loss) / period
    - 最后 RSI = 100 - 100 / (1 + RS)，RS = avg_gain / avg_loss
    """
    delta = close_series.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)

    rsi = pd.Series(index=close_series.index, dtype="float64")
    if len(close_series) <= period:
        return rsi

    first_idx = period

    avg_gain = gains.iloc[1 : period + 1].mean()
    avg_loss = losses.iloc[1 : period + 1].mean()

    if avg_loss == 0:
        rsi.iloc[first_idx] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi.iloc[first_idx] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(first_idx + 1, len(close_series)):
        gain_today = gains.iloc[i]
        loss_today = losses.iloc[i]

        avg_gain = ((avg_gain * (period - 1)) + gain_today) / period
        avg_loss = ((avg_loss * (period - 1)) + loss_today) / period

        if avg_loss == 0:
            rsi.iloc[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi.iloc[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def build_rsi_df(price_df: pd.DataFrame) -> pd.DataFrame:
    """
    基于价格 DataFrame 计算 RSI6 / RSI12。
    """
    if price_df.empty:
        return pd.DataFrame(columns=["date", "rsi6", "rsi12"])

    df = price_df.copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["rsi6"] = compute_wilder_rsi(df["close"], period=6)
    df["rsi12"] = compute_wilder_rsi(df["close"], period=12)
    return df


def save_rsi_table(df: pd.DataFrame) -> int:
    """写入 btc_rsi，返回写入条数。"""
    rsi_rows = 0

    with SessionLocal() as session:
        for _, row in df.iterrows():
            date = row["date"]
            rsi_payload: dict[str, Any] = {
                "date": date,
                "rsi6": float(row["rsi6"]) if pd.notna(row["rsi6"]) else None,
                "rsi12": float(row["rsi12"]) if pd.notna(row["rsi12"]) else None,
            }
            upsert_by_date(session, BtcRsi, rsi_payload)
            rsi_rows += 1

        session.commit()

    return rsi_rows


def run_rsi_pipeline() -> dict[str, Any]:
    """
    一键执行完整流程，并返回统计结果。
    """
    init_db()
    price_df = load_price_history()
    if price_df.empty:
        raise RuntimeError("btc_price 表没有数据，请先执行价格抓取脚本。")

    result_df = build_rsi_df(price_df)
    rsi_rows = save_rsi_table(result_df)

    latest = result_df.iloc[-1]

    def _f(col: str) -> float | None:
        return None if pd.isna(latest[col]) else float(latest[col])

    return {
        "btc_rsi_rows_written": rsi_rows,
        "latest_date": latest["date"],
        "latest_rsi6": _f("rsi6"),
        "latest_rsi12": _f("rsi12"),
    }


if __name__ == "__main__":
    summary = run_rsi_pipeline()
    print("[完成] RSI 计算与入库成功")
    print(f"- btc_rsi 写入条数: {summary['btc_rsi_rows_written']}")
    print(f"- 最新日期: {summary['latest_date']}")
    print(f"- RSI6: {summary['latest_rsi6']}")
    print(f"- RSI12: {summary['latest_rsi12']}")
