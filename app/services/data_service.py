"""
数据服务层：
负责把 DataFrame 写入数据库，以及从数据库读取数据。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import MarketData


def _to_datetime_safe(value) -> datetime:
    """
    安全转换时间字段为 datetime。
    """
    return pd.to_datetime(value).to_pydatetime()


def _to_float_or_none(value):
    """
    安全转换为 float。
    - NaN / 空值 -> None
    - 可转换值 -> float
    - 异常值 -> None
    """
    if pd.isna(value):
        return None
    try:
        return float(value)
    except Exception:
        return None


def save_market_dataframe(
    session: Session,
    market: str,
    symbol: str,
    df: pd.DataFrame,
) -> int:
    """
    批量写入数据，返回成功写入（或更新）的行数。

    这里用了 SQLite 的 upsert（冲突就更新），
    所以重复抓取同一时间的数据不会报错。
    """
    if df.empty:
        return 0

    rows = []
    for _, row in df.iterrows():
        # 有些数据源会返回空行或异常行（如 timestamp 为 NaN），这里直接跳过。
        if "timestamp" not in row or pd.isna(row["timestamp"]):
            continue

        try:
            ts = _to_datetime_safe(row["timestamp"])
        except Exception:
            # 时间字段解析失败时跳过该行，避免整批写入报错
            continue

        rows.append(
            {
                "market": market,
                "symbol": symbol,
                "timestamp": ts,
                "open": _to_float_or_none(row.get("open")),
                "high": _to_float_or_none(row.get("high")),
                "low": _to_float_or_none(row.get("low")),
                "close": _to_float_or_none(row.get("close")),
                "volume": _to_float_or_none(row.get("volume")),
            }
        )

    # 全部是无效行时，直接返回 0，不执行 SQL
    if not rows:
        return 0

    stmt = sqlite_insert(MarketData).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["market", "symbol", "timestamp"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
        },
    )

    result = session.execute(stmt)
    session.commit()

    # rowcount 在 sqlite 下可能是 -1，这里做保护。
    return max(result.rowcount or 0, 0)


def query_market_data(
    session: Session,
    market: str,
    symbol: str,
) -> pd.DataFrame:
    """
    从数据库读取某个 market + symbol 的全部数据（按时间升序）。
    """
    stmt = (
        select(MarketData)
        .where(MarketData.market == market, MarketData.symbol == symbol)
        .order_by(MarketData.timestamp.asc())
    )
    records = session.scalars(stmt).all()

    if not records:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    data = [
        {
            "timestamp": r.timestamp,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for r in records
    ]
    return pd.DataFrame(data)
