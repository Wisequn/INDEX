"""
BTC 长周期均线计算脚本（Prompt 7）。

本脚本做两件事：
1) 计算 4年均线（1458日）相关数据，写入 btc_4y_ma
2) 计算 200周均线（1400日）相关数据，写入 btc_200w_ma

数据来源：
- 直接读取本地数据库 btc_price（close）
- 不依赖外部 API
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from scipy.stats import percentileofscore
from sqlalchemy import func, select

from app.db.database import SessionLocal, init_db, upsert_by_date
from app.db.models import Btc200wMa, Btc4yMa, BtcPrice

# 两条均线窗口
WINDOW_4Y = 1458
WINDOW_200W = 1400


def load_price_history() -> pd.DataFrame:
    """
    从 btc_price 读取历史 close，按日期升序。
    """
    with SessionLocal() as session:
        rows = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.asc())).all()

    if not rows:
        return pd.DataFrame(columns=["date", "close"])

    df = pd.DataFrame(rows, columns=["date", "close"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df


def _rolling_percentile(values: pd.Series, window_days: int | None) -> pd.Series:
    """
    计算某列数值在历史窗口内的百分位（0-100）。

    参数：
    - values: 指标序列（例如 price_to_4y_ma）
    - window_days:
      - 365：过去1年
      - 1460：过去4年
      - None：全部历史
    """
    result = pd.Series(index=values.index, dtype="float64")

    for i in range(len(values)):
        current = values.iloc[i]
        if pd.isna(current):
            continue

        if window_days is None:
            window = values.iloc[: i + 1]
        else:
            start = max(0, i - window_days + 1)
            window = values.iloc[start : i + 1]

        window = window.dropna()
        if window.empty:
            continue

        result.iloc[i] = float(percentileofscore(window.to_numpy(), current, kind="rank"))

    return result


def build_4y_ma_df(price_df: pd.DataFrame) -> pd.DataFrame:
    """
    计算 4年均线相关字段：
    - ma_value（1458日SMA）
    - price_to_4y_ma
    - 三个百分位
    """
    if price_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "ma_value",
                "price_to_4y_ma",
                "p4yma_pct_1y",
                "p4yma_pct_4y",
                "p4yma_pct_all",
            ]
        )

    df = price_df.copy()
    df["ma_value"] = df["close"].rolling(window=WINDOW_4Y, min_periods=WINDOW_4Y).mean()
    df["price_to_4y_ma"] = df["close"] / df["ma_value"]

    # 计算百分位（基于 price_to_4y_ma）
    df["p4yma_pct_1y"] = _rolling_percentile(df["price_to_4y_ma"], window_days=365)
    df["p4yma_pct_4y"] = _rolling_percentile(df["price_to_4y_ma"], window_days=1460)
    df["p4yma_pct_all"] = _rolling_percentile(df["price_to_4y_ma"], window_days=None)

    # 要求：ma_value 为 NULL 的日期不入库
    df = df[df["ma_value"].notna()].copy()
    return df[
        [
            "date",
            "ma_value",
            "price_to_4y_ma",
            "p4yma_pct_1y",
            "p4yma_pct_4y",
            "p4yma_pct_all",
        ]
    ]


def build_200w_ma_df(price_df: pd.DataFrame) -> pd.DataFrame:
    """
    计算 200周均线相关字段：
    - ma_value（1400日SMA）
    - price_to_200w_ma
    - 三个百分位
    """
    if price_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "ma_value",
                "price_to_200w_ma",
                "p200wma_pct_1y",
                "p200wma_pct_4y",
                "p200wma_pct_all",
            ]
        )

    df = price_df.copy()
    df["ma_value"] = df["close"].rolling(window=WINDOW_200W, min_periods=WINDOW_200W).mean()
    df["price_to_200w_ma"] = df["close"] / df["ma_value"]

    # 计算百分位（基于 price_to_200w_ma）
    df["p200wma_pct_1y"] = _rolling_percentile(df["price_to_200w_ma"], window_days=365)
    df["p200wma_pct_4y"] = _rolling_percentile(df["price_to_200w_ma"], window_days=1460)
    df["p200wma_pct_all"] = _rolling_percentile(df["price_to_200w_ma"], window_days=None)

    # 要求：ma_value 为 NULL 的日期不入库
    df = df[df["ma_value"].notna()].copy()
    return df[
        [
            "date",
            "ma_value",
            "price_to_200w_ma",
            "p200wma_pct_1y",
            "p200wma_pct_4y",
            "p200wma_pct_all",
        ]
    ]


def save_4y_ma(df: pd.DataFrame) -> int:
    """
    upsert 写入 btc_4y_ma，返回写入条数。
    """
    if df.empty:
        return 0

    count = 0
    with SessionLocal() as session:
        for _, row in df.iterrows():
            upsert_by_date(
                session=session,
                model=Btc4yMa,
                row_data={
                    "date": row["date"],
                    "ma_value": float(row["ma_value"]),
                    "price_to_4y_ma": float(row["price_to_4y_ma"]),
                    "p4yma_pct_1y": float(row["p4yma_pct_1y"]) if pd.notna(row["p4yma_pct_1y"]) else None,
                    "p4yma_pct_4y": float(row["p4yma_pct_4y"]) if pd.notna(row["p4yma_pct_4y"]) else None,
                    "p4yma_pct_all": float(row["p4yma_pct_all"]) if pd.notna(row["p4yma_pct_all"]) else None,
                },
            )
            count += 1
        session.commit()
    return count


def save_200w_ma(df: pd.DataFrame) -> int:
    """
    upsert 写入 btc_200w_ma，返回写入条数。
    """
    if df.empty:
        return 0

    count = 0
    with SessionLocal() as session:
        for _, row in df.iterrows():
            upsert_by_date(
                session=session,
                model=Btc200wMa,
                row_data={
                    "date": row["date"],
                    "ma_value": float(row["ma_value"]),
                    "price_to_200w_ma": float(row["price_to_200w_ma"]),
                    "p200wma_pct_1y": float(row["p200wma_pct_1y"]) if pd.notna(row["p200wma_pct_1y"]) else None,
                    "p200wma_pct_4y": float(row["p200wma_pct_4y"]) if pd.notna(row["p200wma_pct_4y"]) else None,
                    "p200wma_pct_all": float(row["p200wma_pct_all"]) if pd.notna(row["p200wma_pct_all"]) else None,
                },
            )
            count += 1
        session.commit()
    return count


def run_ma_pipeline() -> dict[str, Any]:
    """
    一键执行：
    1) 读价格
    2) 算两条均线
    3) 写两张表
    4) 返回验收信息
    """
    init_db()
    price_df = load_price_history()
    if price_df.empty:
        raise RuntimeError("btc_price 表没有数据，请先执行价格抓取。")

    df_4y = build_4y_ma_df(price_df)
    df_200w = build_200w_ma_df(price_df)

    written_4y = save_4y_ma(df_4y)
    written_200w = save_200w_ma(df_200w)

    with SessionLocal() as session:
        cnt_4y = session.scalar(select(func.count()).select_from(Btc4yMa)) or 0
        min_4y = session.scalar(select(func.min(Btc4yMa.date)))

        cnt_200w = session.scalar(select(func.count()).select_from(Btc200wMa)) or 0
        min_200w = session.scalar(select(func.min(Btc200wMa.date)))

        latest_4y_two = session.execute(select(Btc4yMa).order_by(Btc4yMa.date.desc()).limit(2)).scalars().all()
        latest_200w_two = session.execute(select(Btc200wMa).order_by(Btc200wMa.date.desc()).limit(2)).scalars().all()

    return {
        "written_4y": written_4y,
        "written_200w": written_200w,
        "count_4y": int(cnt_4y),
        "min_date_4y": min_4y,
        "count_200w": int(cnt_200w),
        "min_date_200w": min_200w,
        "latest_4y": latest_4y_two[0] if len(latest_4y_two) >= 1 else None,
        "prev_4y": latest_4y_two[1] if len(latest_4y_two) >= 2 else None,
        "latest_200w": latest_200w_two[0] if len(latest_200w_two) >= 1 else None,
        "prev_200w": latest_200w_two[1] if len(latest_200w_two) >= 2 else None,
    }


if __name__ == "__main__":
    summary = run_ma_pipeline()
    print("[完成] MA 计算与入库成功")
    print(f"- btc_4y_ma 写入条数: {summary['written_4y']}")
    print(f"- btc_200w_ma 写入条数: {summary['written_200w']}")
    print(f"- btc_4y_ma 总条数: {summary['count_4y']}, 最早日期: {summary['min_date_4y']}")
    print(f"- btc_200w_ma 总条数: {summary['count_200w']}, 最早日期: {summary['min_date_200w']}")

    if summary["latest_4y"] is not None:
        r = summary["latest_4y"]
        print(
            f"- 4y 最新: {r.date}, ma_value={r.ma_value}, price_to_4y_ma={r.price_to_4y_ma}, "
            f"pct_1y={r.p4yma_pct_1y}, pct_4y={r.p4yma_pct_4y}, pct_all={r.p4yma_pct_all}"
        )
    if summary["prev_4y"] is not None:
        r = summary["prev_4y"]
        print(
            f"- 4y 前一日: {r.date}, ma_value={r.ma_value}, price_to_4y_ma={r.price_to_4y_ma}, "
            f"pct_1y={r.p4yma_pct_1y}, pct_4y={r.p4yma_pct_4y}, pct_all={r.p4yma_pct_all}"
        )

    if summary["latest_200w"] is not None:
        r = summary["latest_200w"]
        print(
            f"- 200w 最新: {r.date}, ma_value={r.ma_value}, price_to_200w_ma={r.price_to_200w_ma}, "
            f"pct_1y={r.p200wma_pct_1y}, pct_4y={r.p200wma_pct_4y}, pct_all={r.p200wma_pct_all}"
        )
    if summary["prev_200w"] is not None:
        r = summary["prev_200w"]
        print(
            f"- 200w 前一日: {r.date}, ma_value={r.ma_value}, price_to_200w_ma={r.price_to_200w_ma}, "
            f"pct_1y={r.p200wma_pct_1y}, pct_4y={r.p200wma_pct_4y}, pct_all={r.p200wma_pct_all}"
        )
