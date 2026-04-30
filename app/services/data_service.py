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
        rows.append(
            {
                "market": market,
                "symbol": symbol,
                "timestamp": _to_datetime_safe(row["timestamp"]),
                "open": float(row["open"]) if pd.notna(row["open"]) else None,
                "high": float(row["high"]) if pd.notna(row["high"]) else None,
                "low": float(row["low"]) if pd.notna(row["low"]) else None,
                "close": float(row["close"]) if pd.notna(row["close"]) else None,
                "volume": float(row["volume"]) if pd.notna(row["volume"]) else None,
            }
        )

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
