"""
BTC 指标分析页面。

运行方式：
streamlit run app/ui/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

# 兼容“从任意目录启动 streamlit”
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.database import SessionLocal, init_db
from app.db.models import Btc200wMa, Btc4yMa, BtcAhr999, BtcFearGreed, BtcPrice, BtcRsi, BtcRsiPercentile

init_db()


def _load_all_history() -> pd.DataFrame:
    """
    一次性读取 BTC 相关表，并按 date 合并成一个总表。
    默认使用“全历史数据”。
    """
    with SessionLocal() as session:
        price_rows = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.asc())).all()
        rsi_rows = session.execute(select(BtcRsi.date, BtcRsi.rsi6, BtcRsi.rsi12).order_by(BtcRsi.date.asc())).all()
        rsi_pct_rows = session.execute(
            select(
                BtcRsiPercentile.date,
                BtcRsiPercentile.rsi6_pct_1y,
                BtcRsiPercentile.rsi12_pct_1y,
            ).order_by(BtcRsiPercentile.date.asc())
        ).all()
        fg_rows = session.execute(
            select(BtcFearGreed.date, BtcFearGreed.value, BtcFearGreed.fg_pct_1y).order_by(BtcFearGreed.date.asc())
        ).all()
        ahr_rows = session.execute(
            select(
                BtcAhr999.date,
                BtcAhr999.ahr999_value,
                BtcAhr999.ahr999_pct_1y,
                BtcAhr999.ahr999_pct_4y,
                BtcAhr999.ahr999_pct_all,
            ).order_by(BtcAhr999.date.asc())
        ).all()
        ma4y_rows = session.execute(
            select(
                Btc4yMa.date,
                Btc4yMa.ma_value,
                Btc4yMa.price_to_4y_ma,
            ).order_by(Btc4yMa.date.asc())
        ).all()
        ma200w_rows = session.execute(
            select(
                Btc200wMa.date,
                Btc200wMa.ma_value,
                Btc200wMa.price_to_200w_ma,
            ).order_by(Btc200wMa.date.asc())
        ).all()

    df_price = pd.DataFrame(price_rows, columns=["date", "BTC价格"])
    if df_price.empty:
        return pd.DataFrame()

    df = df_price.copy()
    merge_list = [
        (rsi_rows, ["date", "RSI6", "RSI12"]),
        (rsi_pct_rows, ["date", "RSI6-1Y%", "RSI12-1Y%"]),
        (fg_rows, ["date", "恐惧贪婪", "恐惧贪婪-1Y%"]),
        (ahr_rows, ["date", "Ahr999", "Ahr999-1Y%", "Ahr999-4Y%", "Ahr999-ALL%"]),
        (ma4y_rows, ["date", "4年均线", "价格/4年均线"]),
        (ma200w_rows, ["date", "200周均线", "价格/200周均线"]),
    ]

    for rows, cols in merge_list:
        part = pd.DataFrame(rows, columns=cols)
        if not part.empty:
            df = df.merge(part, on="date", how="left")

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _build_figure(df: pd.DataFrame, selected_series: list[str]) -> go.Figure:
    """
    把用户选择的指标画在同一张图上。
    - 主轴（左）：价格类
    - 次轴（右）：指标类/百分位类
    """
    fig = go.Figure()

    price_axis_names = {"BTC价格", "4年均线", "200周均线"}

    for name in selected_series:
        if name not in df.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df[name],
                mode="lines",
                name=name,
                yaxis="y" if name in price_axis_names else "y2",
            )
        )

    fig.update_layout(
        template="plotly_dark",
        xaxis_title="日期",
        yaxis=dict(title="价格（USD）", type="log"),
        yaxis2=dict(title="指标值 / 百分位", overlaying="y", side="right"),
        legend=dict(orientation="h"),
        margin=dict(l=10, r=10, t=10, b=10),
        hovermode="x unified",
    )
    # 关闭图内底部缩略滑块（页面只保留一个主时间滑块）
    fig.update_xaxes(
        rangeslider=dict(visible=False),
    )
    return fig


def _normalize_base_100(df: pd.DataFrame, selected_series: list[str]) -> pd.DataFrame:
    """
    把所选序列归一化到“首个有效点=100”，用于不同量纲的趋势对比。
    """
    out = df[["date"]].copy()
    # 归一化模式下：对“当前选中的所有序列”统一做归一化
    # （用户选择什么，就归一化什么，避免比例不统一）
    for name in selected_series:
        if name not in df.columns:
            continue
        s = pd.to_numeric(df[name], errors="coerce")

        first_valid = s.dropna()
        if first_valid.empty:
            out[name] = s
            continue
        base = first_valid.iloc[0]
        if base == 0:
            out[name] = s
        else:
            out[name] = s / base * 100.0
    return out


def _normalize_minmax_100(df: pd.DataFrame, selected_series: list[str]) -> pd.DataFrame:
    """
    区间归一化：把每条序列在当前窗口内映射到 0~100。
    更适合“不同量级指标放一张图比较波动”。
    """
    out = df[["date"]].copy()
    for name in selected_series:
        if name not in df.columns:
            continue
        s = pd.to_numeric(df[name], errors="coerce")
        valid = s.dropna()
        if valid.empty:
            out[name] = s
            continue
        vmin = valid.min()
        vmax = valid.max()
        if vmax == vmin:
            out[name] = 50.0
        else:
            out[name] = (s - vmin) / (vmax - vmin) * 100.0
    return out


def _build_figure_normalized(df: pd.DataFrame, selected_series: list[str], normalize_method: str) -> go.Figure:
    """
    归一化展示模式：所有序列共用一个轴（起点=100），更适合趋势对比。
    """
    fig = go.Figure()
    if normalize_method == "区间归一化(0-100，推荐)":
        norm_df = _normalize_minmax_100(df, selected_series)
    else:
        norm_df = _normalize_base_100(df, selected_series)

    for name in selected_series:
        if name not in norm_df.columns:
            continue
        # 原始值用于 hover 展示，避免把“归一化值”误解成真实价格
        raw_series = pd.to_numeric(df[name], errors="coerce")

        if name == "BTC价格":
            hover_template = (
                "日期: %{x|%Y-%m-%d}<br>"
                "BTC价格(真实): %{customdata:,.0f} USD<br>"
                "归一化指数: %{y:.2f}<extra></extra>"
            )
        else:
            hover_template = (
                "日期: %{x|%Y-%m-%d}<br>"
                f"{name}(原始): " "%{customdata:.4f}<br>"
                "归一化指数: %{y:.2f}<extra></extra>"
            )

        fig.add_trace(
            go.Scatter(
                x=norm_df["date"],
                y=norm_df[name],
                mode="lines",
                name=name,
                customdata=raw_series,
                hovertemplate=hover_template,
            )
        )

    fig.update_layout(
        template="plotly_dark",
        xaxis_title="日期",
        yaxis=dict(title="归一化指数"),
        legend=dict(orientation="h"),
        margin=dict(l=10, r=10, t=10, b=10),
        hovermode="x unified",
    )
    fig.update_xaxes(
        rangeslider=dict(visible=False),
    )
    return fig


st.set_page_config(page_title="BTC- INDEX分析", layout="wide")
st.title("BTC- INDEX分析")
st.caption("默认展示最近1年；可在图上拖拽缩放查看任意时间段。")

df_all = _load_all_history()
if df_all.empty:
    st.warning("当前数据库没有 BTC 价格数据。请先完成初始化抓取。")
    st.stop()

series_options = [
    "BTC价格",
    "4年均线",
    "200周均线",
    "RSI6",
    "RSI12",
    "RSI6-1Y%",
    "RSI12-1Y%",
    "恐惧贪婪",
    "恐惧贪婪-1Y%",
    "Ahr999",
    "Ahr999-1Y%",
    "Ahr999-4Y%",
    "Ahr999-ALL%",
    "价格/4年均线",
    "价格/200周均线",
]
series_options = [x for x in series_options if x in df_all.columns]

# 默认视窗定位最近1年（但图中仍加载全历史，便于向前拖拽查看）
max_date = df_all["date"].max()
one_year_ago = max_date - pd.Timedelta(days=365)

mode = st.radio(
    "图表展示模式",
    options=["原值双轴（价格+指标）", "归一化对比（推荐）"],
    index=1,
    horizontal=True,
    help="归一化模式会把你当前选择的所有序列重算为起点=100，便于统一对比。",
)

normalize_method = st.radio(
    "归一化算法",
    options=["区间归一化(0-100，推荐)", "起点归一化(首点=100)"],
    index=0,
    horizontal=True,
    help="区间归一化会按当前时间窗口的高低点重算，避免有些指标被压成直线。",
)

selected = st.multiselect(
    "选择要展示的数据（可多选，可随时开关）",
    options=series_options,
    default=[x for x in ["BTC价格", "Ahr999", "RSI6", "价格/4年均线"] if x in series_options],
)

# 使用“日期序列滑块”替代 datetime slider，兼容性更稳，拖动到历史区间更顺畅
all_dates = sorted(df_all["date"].dt.date.unique().tolist())
if "norm_window_range" not in st.session_state:
    st.session_state["norm_window_range"] = (one_year_ago.date(), max_date.date())

# 顶部“快捷时间按钮”（分段样式），点击后更新主滑块区间
quick_options = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "4Y", "ALL"]
if "quick_window" not in st.session_state:
    st.session_state["quick_window"] = "1Y"
if "quick_window_applied" not in st.session_state:
    st.session_state["quick_window_applied"] = None

quick_window = st.segmented_control(
    "快捷时间",
    options=quick_options,
    default=st.session_state["quick_window"],
    key="quick_window",
    help="点击后自动更新下面的时间滑块区间。",
)

if quick_window and st.session_state["quick_window_applied"] != quick_window:
    end_d = max_date.date()
    if quick_window == "ALL":
        start_d = all_dates[0]
    elif quick_window.endswith("M"):
        months = int(quick_window[:-1])
        start_d = (max_date - pd.DateOffset(months=months)).date()
    else:
        years = int(quick_window[:-1])
        start_d = (max_date - pd.DateOffset(years=years)).date()
    if start_d < all_dates[0]:
        start_d = all_dates[0]
    st.session_state["norm_window_range"] = (start_d, end_d)
    st.session_state["quick_window_applied"] = quick_window

start_d, end_d = st.session_state["norm_window_range"]

df_window = df_all[(df_all["date"].dt.date >= start_d) & (df_all["date"].dt.date <= end_d)].copy()
if df_window.empty:
    df_window = df_all.copy()

if not selected:
    st.info("请至少选择一个数据序列。")
else:
    if mode == "归一化对比（推荐）":
        fig = _build_figure_normalized(df_window, selected, normalize_method)
    else:
        fig = _build_figure(df_all, selected)
        # 原值模式默认视窗仍定位最近1年，但全历史已加载，可往前拖
        fig.update_xaxes(range=[one_year_ago, max_date])
    st.plotly_chart(fig, use_container_width=True)

    # 主时间滑块移动到图形下方
    st.select_slider(
        "归一化时间窗口（拖动左右端点选择，支持全历史）",
        options=all_dates,
        value=st.session_state["norm_window_range"],
        key="norm_window_range",
    )

st.subheader("指标说明表")
info_rows = [
    {"指标代码": "close", "中文名称": "BTC价格", "指标解释": "比特币当日收盘价（USD）"},
    {"指标代码": "rsi6", "中文名称": "RSI6", "指标解释": "6日相对强弱指标，反映短周期动量"},
    {"指标代码": "rsi12", "中文名称": "RSI12", "指标解释": "12日相对强弱指标，反映中短周期动量"},
    {"指标代码": "rsi6_pct_1y", "中文名称": "RSI6-1Y%", "指标解释": "RSI6 在过去1年中的历史百分位（0-100）"},
    {"指标代码": "rsi12_pct_1y", "中文名称": "RSI12-1Y%", "指标解释": "RSI12 在过去1年中的历史百分位（0-100）"},
    {"指标代码": "value", "中文名称": "恐惧贪婪", "指标解释": "恐惧贪婪指数原值（0-100）"},
    {"指标代码": "fg_pct_1y", "中文名称": "恐惧贪婪-1Y%", "指标解释": "恐惧贪婪在过去1年中的历史百分位"},
    {"指标代码": "ahr999_value", "中文名称": "Ahr999", "指标解释": "Ahr999估值指标（低于0.45偏低估，高于1.2偏高估）"},
    {"指标代码": "ahr999_pct_1y", "中文名称": "Ahr999-1Y%", "指标解释": "Ahr999 在过去1年的历史百分位"},
    {"指标代码": "ahr999_pct_4y", "中文名称": "Ahr999-4Y%", "指标解释": "Ahr999 在过去4年的历史百分位"},
    {"指标代码": "ahr999_pct_all", "中文名称": "Ahr999-ALL%", "指标解释": "Ahr999 在全历史中的百分位"},
    {"指标代码": "ma_value(4y)", "中文名称": "4年均线", "指标解释": "1458日简单移动平均线"},
    {"指标代码": "price_to_4y_ma", "中文名称": "价格/4年均线", "指标解释": "当前价格相对4年均线的倍数"},
    {"指标代码": "ma_value(200w)", "中文名称": "200周均线", "指标解释": "1400日简单移动平均线"},
    {"指标代码": "price_to_200w_ma", "中文名称": "价格/200周均线", "指标解释": "当前价格相对200周均线的倍数"},
]
st.dataframe(pd.DataFrame(info_rows), use_container_width=True, hide_index=True)

st.subheader("最新数据预览")
st.dataframe(df_all.tail(20), use_container_width=True)
