"""
Streamlit 主页面。

运行方式：
streamlit run app/ui/main.py
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from app.db.base import SessionLocal
from app.services.data_service import query_market_data, save_market_dataframe
from app.services.fetchers import fetch_cn_history, fetch_crypto_history, fetch_us_history

st.set_page_config(page_title="Index Monitor", layout="wide")
st.title("Index Monitor - 本地量化数据平台")
st.caption("抓取 -> 入库(SQLite) -> 展示（新手友好演示版）")

# 1) 基础输入区域
col1, col2, col3 = st.columns(3)

with col1:
    market = st.selectbox(
        "选择市场",
        options=["crypto", "us", "cn"],
        help="crypto=加密货币, us=美股, cn=A股",
    )

with col2:
    default_symbol = "BTC-USD" if market == "crypto" else ("AAPL" if market == "us" else "000001.SZ")
    symbol = st.text_input("输入代码", value=default_symbol)

with col3:
    today = date.today()
    start_date = st.date_input("开始日期", value=today - timedelta(days=365))
    end_date = st.date_input("结束日期", value=today)

fetch_button = st.button("抓取并保存到数据库", type="primary")
load_button = st.button("只从数据库读取并展示")


def fetch_by_market(selected_market: str, selected_symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    根据市场类型分发到不同抓取函数。
    """
    if selected_market == "crypto":
        return fetch_crypto_history(selected_symbol, start, end)
    if selected_market == "us":
        return fetch_us_history(selected_symbol, start, end)
    return fetch_cn_history(selected_symbol, start, end)


if fetch_button:
    try:
        with st.spinner("正在抓取并保存数据，请稍候..."):
            df = fetch_by_market(market, symbol, str(start_date), str(end_date))

            with SessionLocal() as session:
                affected_rows = save_market_dataframe(session, market, symbol, df)

        st.success(f"抓取并写入完成。处理行数：{affected_rows}")
    except Exception as e:
        st.error(f"执行失败：{e}")


if load_button or fetch_button:
    with SessionLocal() as session:
        db_df = query_market_data(session, market, symbol)

    if db_df.empty:
        st.warning("数据库里还没有这组数据。请先点击“抓取并保存到数据库”。")
    else:
        st.subheader(f"{market} - {symbol} 收盘价走势")
        chart_df = db_df.set_index("timestamp")[["close"]]
        st.line_chart(chart_df)

        st.subheader("原始数据预览（最近 20 行）")
        st.dataframe(db_df.tail(20), use_container_width=True)
