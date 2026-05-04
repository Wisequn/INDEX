"""
RSI 指标计算脚本（Prompt 4）。

本脚本做 3 件事：
1) 从 btc_price 读取收盘价，按日期从早到晚排序
2) 使用标准 Wilder 平滑算法计算 RSI6 / RSI12，并写入 btc_rsi（upsert）
3) 计算 RSI 百分位并写入 btc_rsi_percentile（upsert）

说明：
- 百分位使用 scipy.stats.percentileofscore，kind='rank'（封装见 btc/percentile_common.py）
- 百分位窗口是「当天往前 N 个日历日（含当天）」；1Y=365、2Y=730、3Y=1095、4Y=1460
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select

from app.db.database import SessionLocal, init_db, upsert_by_date
from app.db.models import BtcPrice, BtcRsi, BtcRsiPercentile
from btc.percentile_common import add_multi_year_percentiles, rolling_percentile_series


def load_price_history() -> pd.DataFrame:
    """
    从数据库读取 btc_price 的历史收盘价，并按日期升序排序。
    """
    with SessionLocal() as session:
        rows = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.asc())).all()

    if not rows:
        return pd.DataFrame(columns=["date", "close"])

    return pd.DataFrame(rows, columns=["date", "close"])


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

    # 第一个可计算 RSI 的位置（0-based）
    first_idx = period

    # 初始平均涨跌幅（使用第 1 到 period 天的变动，共 period 个 delta）
    avg_gain = gains.iloc[1 : period + 1].mean()
    avg_loss = losses.iloc[1 : period + 1].mean()

    # 写入第一天 RSI
    if avg_loss == 0:
        rsi.iloc[first_idx] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi.iloc[first_idx] = 100.0 - (100.0 / (1.0 + rs))

    # 后续天数使用 Wilder 平滑递推
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


def build_rsi_and_percentile_df(price_df: pd.DataFrame) -> pd.DataFrame:
    """
    基于价格 DataFrame 计算 RSI 与对应百分位，返回完整结果表。
    """
    if price_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "rsi6",
                "rsi12",
                "rsi6_pct_1y",
                "rsi6_pct_2y",
                "rsi6_pct_3y",
                "rsi6_pct_4y",
                "rsi6_pct_all",
                "rsi12_pct_1y",
                "rsi12_pct_2y",
                "rsi12_pct_3y",
                "rsi12_pct_4y",
                "rsi12_pct_all",
            ]
        )

    df = price_df.copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    # 计算两条 RSI（Wilder）
    df["rsi6"] = compute_wilder_rsi(df["close"], period=6)
    df["rsi12"] = compute_wilder_rsi(df["close"], period=12)

    # RSI6 / RSI12：1Y~4Y 滚动百分位 + 全历史百分位（算法见 btc/percentile_common.py）
    add_multi_year_percentiles(df, "rsi6", "rsi6", years=(1, 2, 3, 4))
    df["rsi6_pct_all"] = rolling_percentile_series(df["rsi6"], None)

    add_multi_year_percentiles(df, "rsi12", "rsi12", years=(1, 2, 3, 4))
    df["rsi12_pct_all"] = rolling_percentile_series(df["rsi12"], None)

    return df


def save_rsi_tables(df: pd.DataFrame) -> tuple[int, int]:
    """
    把结果写入两张表：
    - btc_rsi
    - btc_rsi_percentile

    返回：
    - btc_rsi 写入条数
    - btc_rsi_percentile 写入条数
    """
    rsi_rows = 0
    pct_rows = 0

    with SessionLocal() as session:
        for _, row in df.iterrows():
            date = row["date"]

            # 表1：btc_rsi
            rsi_payload: dict[str, Any] = {
                "date": date,
                "rsi6": float(row["rsi6"]) if pd.notna(row["rsi6"]) else None,
                "rsi12": float(row["rsi12"]) if pd.notna(row["rsi12"]) else None,
            }
            upsert_by_date(session, BtcRsi, rsi_payload)
            rsi_rows += 1

            # 表2：btc_rsi_percentile
            pct_payload: dict[str, Any] = {
                "date": date,
                "rsi6_pct_1y": float(row["rsi6_pct_1y"]) if pd.notna(row["rsi6_pct_1y"]) else None,
                "rsi6_pct_2y": float(row["rsi6_pct_2y"]) if pd.notna(row["rsi6_pct_2y"]) else None,
                "rsi6_pct_3y": float(row["rsi6_pct_3y"]) if pd.notna(row["rsi6_pct_3y"]) else None,
                "rsi6_pct_4y": float(row["rsi6_pct_4y"]) if pd.notna(row["rsi6_pct_4y"]) else None,
                "rsi6_pct_all": float(row["rsi6_pct_all"]) if pd.notna(row["rsi6_pct_all"]) else None,
                "rsi12_pct_1y": float(row["rsi12_pct_1y"]) if pd.notna(row["rsi12_pct_1y"]) else None,
                "rsi12_pct_2y": float(row["rsi12_pct_2y"]) if pd.notna(row["rsi12_pct_2y"]) else None,
                "rsi12_pct_3y": float(row["rsi12_pct_3y"]) if pd.notna(row["rsi12_pct_3y"]) else None,
                "rsi12_pct_4y": float(row["rsi12_pct_4y"]) if pd.notna(row["rsi12_pct_4y"]) else None,
                "rsi12_pct_all": float(row["rsi12_pct_all"]) if pd.notna(row["rsi12_pct_all"]) else None,
            }
            upsert_by_date(session, BtcRsiPercentile, pct_payload)
            pct_rows += 1

        session.commit()

    return rsi_rows, pct_rows


def run_rsi_pipeline() -> dict[str, Any]:
    """
    一键执行完整流程，并返回统计结果。
    """
    init_db()
    price_df = load_price_history()
    if price_df.empty:
        raise RuntimeError("btc_price 表没有数据，请先执行价格抓取脚本。")

    result_df = build_rsi_and_percentile_df(price_df)
    rsi_rows, pct_rows = save_rsi_tables(result_df)

    latest = result_df.iloc[-1]

    def _f(col: str) -> float | None:
        return None if pd.isna(latest[col]) else float(latest[col])

    return {
        "btc_rsi_rows_written": rsi_rows,
        "btc_rsi_percentile_rows_written": pct_rows,
        "latest_date": latest["date"],
        "latest_rsi6": _f("rsi6"),
        "latest_rsi12": _f("rsi12"),
        "latest_rsi6_pct_1y": _f("rsi6_pct_1y"),
        "latest_rsi6_pct_2y": _f("rsi6_pct_2y"),
        "latest_rsi6_pct_3y": _f("rsi6_pct_3y"),
        "latest_rsi6_pct_4y": _f("rsi6_pct_4y"),
        "latest_rsi6_pct_all": _f("rsi6_pct_all"),
        "latest_rsi12_pct_1y": _f("rsi12_pct_1y"),
        "latest_rsi12_pct_2y": _f("rsi12_pct_2y"),
        "latest_rsi12_pct_3y": _f("rsi12_pct_3y"),
        "latest_rsi12_pct_4y": _f("rsi12_pct_4y"),
        "latest_rsi12_pct_all": _f("rsi12_pct_all"),
    }


if __name__ == "__main__":
    summary = run_rsi_pipeline()
    print("[完成] RSI 计算与入库成功")
    print(f"- btc_rsi 写入条数: {summary['btc_rsi_rows_written']}")
    print(f"- btc_rsi_percentile 写入条数: {summary['btc_rsi_percentile_rows_written']}")
    print(f"- 最新日期: {summary['latest_date']}")
    print(f"- RSI6: {summary['latest_rsi6']}")
    print(f"- RSI12: {summary['latest_rsi12']}")
    print(f"- rsi6_pct_1y: {summary['latest_rsi6_pct_1y']}")
    print(f"- rsi6_pct_4y: {summary['latest_rsi6_pct_4y']}")
    print(f"- rsi6_pct_all: {summary['latest_rsi6_pct_all']}")
    print(f"- rsi12_pct_1y: {summary['latest_rsi12_pct_1y']}")
    print(f"- rsi12_pct_4y: {summary['latest_rsi12_pct_4y']}")
    print(f"- rsi12_pct_all: {summary['latest_rsi12_pct_all']}")
