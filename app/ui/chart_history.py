"""
Streamlit 图表用：全历史一次加载（只读原始列，不在首屏预计算全表百分位）。

- `load_full_chart_dataframe` 使用 @st.cache_data(ttl=3600)，整页共享一份大表。
- 百分位在 `main.py` 的当前窗口按需计算，避免首页冷启动等待过长。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.db.models import Btc200wMa, Btc4yMa, BtcAhr999, BtcFearGreed, BtcPrice, BtcRsi


def _load_raw_merged_from_db() -> pd.DataFrame:
    """只读库里的原始列（价、RSI、恐惧贪婪原值、Ahr999、均线），不做百分位。"""
    with SessionLocal() as session:
        price_rows = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.asc())).all()
        rsi_rows = session.execute(select(BtcRsi.date, BtcRsi.rsi6, BtcRsi.rsi12).order_by(BtcRsi.date.asc())).all()
        fg_rows = session.execute(select(BtcFearGreed.date, BtcFearGreed.value).order_by(BtcFearGreed.date.asc())).all()
        ahr_rows = session.execute(select(BtcAhr999.date, BtcAhr999.ahr999_value).order_by(BtcAhr999.date.asc())).all()
        ma4y_rows = session.execute(
            select(Btc4yMa.date, Btc4yMa.ma_value, Btc4yMa.price_to_4y_ma).order_by(Btc4yMa.date.asc())
        ).all()
        ma200w_rows = session.execute(
            select(Btc200wMa.date, Btc200wMa.ma_value, Btc200wMa.price_to_200w_ma).order_by(Btc200wMa.date.asc())
        ).all()

    df_price = pd.DataFrame(price_rows, columns=["date", "BTC价格"])
    if df_price.empty:
        return pd.DataFrame()

    df = df_price.copy()
    merge_list: list[tuple[list[Any], list[str]]] = [
        (rsi_rows, ["date", "RSI6", "RSI12"]),
        (fg_rows, ["date", "恐惧贪婪"]),
        (ahr_rows, ["date", "Ahr999"]),
        (ma4y_rows, ["date", "4年均线", "价格/4年均线"]),
        (ma200w_rows, ["date", "200周均线", "价格/200周均线"]),
    ]
    for rows, cols in merge_list:
        part = pd.DataFrame(rows, columns=cols)
        if not part.empty:
            df = df.merge(part, on="date", how="left")

    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner="正在加载全历史到内存（每小时刷新一次缓存）…")
def load_full_chart_dataframe() -> pd.DataFrame:
    """
    页面级缓存：一次 SQL 合并（不做首屏全表百分位计算）。

    说明：
    - 切换日期窗口时不要调用本函数，只对返回的 DataFrame 做布尔切片即可。
    - 需要强制刷新时可 st.cache_data.clear() 或调低 ttl。
    """
    init_db()
    return _load_raw_merged_from_db()
