"""
BTC 指标分析页面。

运行方式：
streamlit run app/ui/main.py
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# 兼容“从任意目录启动 streamlit”：项目根必须排在 sys.path 最前，避免误 import 到别处同名 app/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ROOT_STR = str(PROJECT_ROOT)
while _ROOT_STR in sys.path:
    sys.path.remove(_ROOT_STR)
sys.path.insert(0, _ROOT_STR)

from app.db.database import init_db
from app.ui.chart_history import load_full_chart_dataframe
from btc.realtime import get_realtime_values
from btc.percentile_runtime import calculate_dynamic_percentile
from scheduler.alert_engine import (
    build_inputs_realtime,
    calculate_normalized_score_and_position,
    get_factor_scores,
)

# 建表 + 幂等清理旧百分位列（@st.cache_data 内部也会 init_db，此处保证首屏前迁移已执行）
init_db()

# 单一多选框：label → 原值列 或 「区间内动态百分位」（在 df_window 上 expanding 窗口，见 _expanding_percentile_in_date_range）
_SERIES_DEFS: list[dict[str, str]] = [
    {"label": "BTC价格", "kind": "raw", "col": "BTC价格"},
    {"label": "RSI6%", "kind": "pct_range", "col": "RSI6"},
    {"label": "RSI12%", "kind": "pct_range", "col": "RSI12"},
    {"label": "恐惧贪婪%", "kind": "pct_range", "col": "恐惧贪婪"},
    {"label": "Ahr999%", "kind": "pct_range", "col": "Ahr999"},
    {"label": "4年均线", "kind": "raw", "col": "4年均线"},
    {"label": "200周均线", "kind": "raw", "col": "200周均线"},
    {"label": "价格/4年均线", "kind": "raw", "col": "价格/4年均线"},
    {"label": "价格/200周均线", "kind": "raw", "col": "价格/200周均线"},
    {"label": "RSI6", "kind": "raw", "col": "RSI6"},
    {"label": "RSI12", "kind": "raw", "col": "RSI12"},
    {"label": "恐惧贪婪", "kind": "raw", "col": "恐惧贪婪"},
    {"label": "Ahr999", "kind": "raw", "col": "Ahr999"},
]


def _series_labels_available(df: pd.DataFrame) -> list[str]:
    """只列出当前宽表里确有数据列的选项。"""
    labels: list[str] = []
    for d in _SERIES_DEFS:
        if d["col"] in df.columns:
            labels.append(d["label"])
    return labels


def _def_for_label(label: str) -> dict[str, str] | None:
    for d in _SERIES_DEFS:
        if d["label"] == label:
            return d
    return None


def _expanding_percentile_in_date_range(values: pd.Series) -> pd.Series:
    """
    在当前图表切片内做「从切片首日到当日」的扩展窗口百分位（0～100）。
    每一步用 calculate_dynamic_percentile（与 percentile_runtime 一致，rank 口径）。
    """
    s = pd.to_numeric(values, errors="coerce")
    out = pd.Series(index=s.index, dtype="float64")
    n = len(s)
    for i in range(n):
        cur = s.iloc[i]
        if pd.isna(cur):
            continue
        window = s.iloc[: i + 1]
        p = calculate_dynamic_percentile(window, float(cur))
        if p is not None:
            out.iloc[i] = p
    return out


def _build_plot_dataframe(
    df_w: pd.DataFrame, chosen_labels: list[str]
) -> tuple[pd.DataFrame, list[str], dict[str, str]]:
    """
    根据多选标签生成用于绑图的 DataFrame（含 __pct_* 动态列）及列顺序、图例名。
    """
    plot_df = df_w[["date"]].copy()
    selected_cols: list[str] = []
    trace_display_names: dict[str, str] = {}

    for label in chosen_labels:
        d = _def_for_label(label)
        if d is None or d["col"] not in df_w.columns:
            continue
        if d["kind"] == "raw":
            col = d["col"]
            plot_df[col] = df_w[col]
            selected_cols.append(col)
            trace_display_names[col] = label
        else:
            ic = f"__pct_{d['col']}"
            plot_df[ic] = _expanding_percentile_in_date_range(df_w[d["col"]])
            selected_cols.append(ic)
            trace_display_names[ic] = f"{label}（区间内动态百分位）"

    return plot_df, selected_cols, trace_display_names


def _coerce_norm_window_range(
    raw: object,
    all_dates: list[date],
    default: tuple[date, date],
) -> tuple[date, date]:
    """
    解析日期区间为 (lo, hi)。
    Streamlit 的 st.select_slider 区间常存成 list 而非 tuple；只认 tuple 会误判并退回默认一年窗口。
    """
    if not all_dates:
        return default
    bound_lo, bound_hi = all_dates[0], all_dates[-1]
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        try:
            lo = pd.Timestamp(raw[0]).date()
            hi = pd.Timestamp(raw[1]).date()
        except (TypeError, ValueError, OverflowError):
            return default
        if lo > hi:
            lo, hi = hi, lo
        lo = max(lo, bound_lo)
        hi = min(hi, bound_hi)
        if lo <= hi:
            return (lo, hi)
    return default


def _nearest_date_index(d: date, all_dates: list[date]) -> int:
    return min(
        range(len(all_dates)),
        key=lambda i: abs((pd.Timestamp(all_dates[i]) - pd.Timestamp(d)).days),
    )


def _canonicalize_slider_range(
    lo: date, hi: date, all_dates: list[date]
) -> tuple[date, date]:
    """把区间两端映射到 options 里真实存在的 date（与 all_dates 元素一致），避免 select_slider 校验失败被刷回默认。"""
    if not all_dates:
        return lo, hi
    i = _nearest_date_index(lo, all_dates)
    j = _nearest_date_index(hi, all_dates)
    if i > j:
        i, j = j, i
    return (all_dates[i], all_dates[j])


def _norm_window_needs_canonicalize(
    raw: object, canon: tuple[date, date]
) -> bool:
    if not isinstance(raw, (tuple, list)) or len(raw) != 2:
        return True
    try:
        lo = pd.Timestamp(raw[0]).date()
        hi = pd.Timestamp(raw[1]).date()
    except (TypeError, ValueError, OverflowError):
        return True
    if lo > hi:
        lo, hi = hi, lo
    if isinstance(raw, list):
        return True
    return (lo, hi) != canon


def _as_quick_token(qw: object) -> str | None:
    """segmented_control 在部分版本可能返回 list，统一成与 quick_window_applied 可比较的 str。"""
    if qw is None:
        return None
    if isinstance(qw, (list, tuple)) and len(qw) >= 1:
        return str(qw[0])
    return str(qw)


def _build_figure(
    df: pd.DataFrame,
    selected_series: list[str],
    trace_display_names: dict[str, str] | None = None,
    chart_title: str | None = None,
) -> go.Figure:
    """
    把用户选择的指标画在同一张图上。
    - 主轴（左）：价格类
    - 次轴（右）：指标类/百分位类
    """
    fig = go.Figure()
    labels = trace_display_names or {}

    price_axis_names = {"BTC价格", "4年均线", "200周均线"}

    for name in selected_series:
        if name not in df.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df[name],
                mode="lines",
                name=labels.get(name, name),
                yaxis="y" if name in price_axis_names else "y2",
            )
        )

    layout_updates: dict = dict(
        template="plotly_dark",
        xaxis_title="",
        yaxis=dict(title="价格（USD）", type="log"),
        yaxis2=dict(title="指标值 / 百分位", overlaying="y", side="right"),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
        margin=dict(l=10, r=10, t=72, b=0),
        hovermode="x unified",
    )
    if chart_title:
        layout_updates["margin"] = dict(l=10, r=10, t=96, b=0)
        layout_updates["title"] = dict(
            text=chart_title, x=0.02, xanchor="left", font=dict(size=13)
        )
    fig.update_layout(**layout_updates)
    fig.update_xaxes(
        rangeslider=dict(visible=False),
    )
    return fig


# 归一化时用「同一套 vmin/vmax」的序列组：同量纲才可横比，否则会价高线低（各自 min-max 的错觉）
_SHARED_MINMAX_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"BTC价格", "4年均线", "200周均线"}),
    frozenset({"RSI6", "RSI12"}),
    frozenset({"价格/4年均线", "价格/200周均线"}),
)
_SHARED_BASE100_GROUPS: tuple[frozenset[str], ...] = _SHARED_MINMAX_GROUPS


def _group_members_in_selection(group: frozenset[str], selected_series: list[str], df: pd.DataFrame) -> list[str]:
    return [n for n in selected_series if n in group and n in df.columns]


def _combined_minmax(
    df: pd.DataFrame, names: list[str]
) -> tuple[float | None, float | None]:
    """多列数值拼在一起取 min / max；无有效数时返回 (None, None)。"""
    if not names:
        return None, None
    stacked: list[pd.Series] = []
    for n in names:
        stacked.append(pd.to_numeric(df[n], errors="coerce").dropna())
    if not stacked:
        return None, None
    all_vals = pd.concat(stacked, ignore_index=True)
    if all_vals.empty:
        return None, None
    return float(all_vals.min()), float(all_vals.max())


def _apply_minmax_to_series(s: pd.Series, vmin: float, vmax: float) -> pd.Series:
    if vmax == vmin:
        return pd.Series(50.0, index=s.index, dtype="float64")
    return (s - vmin) / (vmax - vmin) * 100.0


def _normalize_base_100(df: pd.DataFrame, selected_series: list[str]) -> pd.DataFrame:
    """
    把所选序列归一化到「锚点行=100」。
    同量纲组（如 BTC 收盘价与均线）共用同一锚点行，避免各线各除各的首点导致不可比。
    """
    out = df[["date"]].copy()
    handled: set[str] = set()

    def _series_one_base(name: str, s: pd.Series) -> None:
        first_valid = s.dropna()
        if first_valid.empty:
            out[name] = s
            return
        base = float(first_valid.iloc[0])
        if base == 0:
            out[name] = s
        else:
            out[name] = s / base * 100.0

    for group in _SHARED_BASE100_GROUPS:
        members = _group_members_in_selection(group, selected_series, df)
        if not members:
            continue
        if len(members) == 1:
            name = members[0]
            s = pd.to_numeric(df[name], errors="coerce")
            _series_one_base(name, s)
            handled.add(name)
            continue
        # 多选同组：取「组内全部有值」的最早一行作共同锚点
        mask = pd.Series(True, index=df.index)
        series_map: dict[str, pd.Series] = {}
        for n in members:
            sn = pd.to_numeric(df[n], errors="coerce")
            series_map[n] = sn
            mask &= sn.notna()
        if not bool(mask.any()):
            for n in members:
                _series_one_base(n, series_map[n])
                handled.add(n)
            continue
        anchor_pos = next(i for i in range(len(mask)) if bool(mask.iloc[i]))
        for n in members:
            s = series_map[n]
            base = float(s.iloc[anchor_pos])
            if base == 0:
                out[n] = s
            else:
                out[n] = s / base * 100.0
            handled.add(n)

    for name in selected_series:
        if name in handled or name not in df.columns:
            continue
        s = pd.to_numeric(df[name], errors="coerce")
        _series_one_base(name, s)
    return out


def _normalize_minmax_100(df: pd.DataFrame, selected_series: list[str]) -> pd.DataFrame:
    """
    区间归一化：把序列在当前窗口内映射到 0~100。
    同量纲组（USD 价格类、RSI、价格比）共用同一 vmin/vmax，与真实大小关系一致。
    其余序列仍各自 min-max（量纲不同不宜硬绑）。
    """
    out = df[["date"]].copy()
    handled: set[str] = set()

    for group in _SHARED_MINMAX_GROUPS:
        members = _group_members_in_selection(group, selected_series, df)
        if not members:
            continue
        vmin, vmax = _combined_minmax(df, members)
        if vmin is None or vmax is None:
            for n in members:
                out[n] = pd.to_numeric(df[n], errors="coerce")
                handled.add(n)
            continue
        for n in members:
            s = pd.to_numeric(df[n], errors="coerce")
            out[n] = _apply_minmax_to_series(s, vmin, vmax)
            handled.add(n)

    for name in selected_series:
        if name in handled or name not in df.columns:
            continue
        s = pd.to_numeric(df[name], errors="coerce")
        valid = s.dropna()
        if valid.empty:
            out[name] = s
            continue
        vmin = float(valid.min())
        vmax = float(valid.max())
        out[name] = _apply_minmax_to_series(s, vmin, vmax)
    return out


def _build_figure_normalized(
    df: pd.DataFrame,
    selected_series: list[str],
    normalize_method: str,
    trace_display_names: dict[str, str] | None = None,
    chart_title: str | None = None,
) -> go.Figure:
    """
    归一化展示模式：所有序列共用一个纵轴。
    价格类与均线类共用线性映射，保证图上高低与 USD 真实大小一致。
    """
    fig = go.Figure()
    labels = trace_display_names or {}

    if normalize_method == "区间归一化(0-100，推荐)":
        norm_df = _normalize_minmax_100(df, selected_series)
    else:
        norm_df = _normalize_base_100(df, selected_series)

    for name in selected_series:
        if name not in norm_df.columns:
            continue
        # 原始值用于 hover 展示，避免把“归一化值”误解成真实价格
        raw_series = pd.to_numeric(df[name], errors="coerce")
        disp = labels.get(name, name)

        if name == "BTC价格":
            hover_template = (
                "日期: %{x|%Y-%m-%d}<br>"
                "BTC价格(真实): %{customdata:,.0f} USD<br>"
                "归一化指数: %{y:.2f}<extra></extra>"
            )
        else:
            hover_template = (
                "日期: %{x|%Y-%m-%d}<br>"
                f"{disp}(原始): " "%{customdata:.4f}<br>"
                "归一化指数: %{y:.2f}<extra></extra>"
            )

        fig.add_trace(
            go.Scatter(
                x=norm_df["date"],
                y=norm_df[name],
                mode="lines",
                name=disp,
                customdata=raw_series,
                hovertemplate=hover_template,
            )
        )

    layout_updates: dict = dict(
        template="plotly_dark",
        xaxis_title="",
        yaxis=dict(title="归一化指数"),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
        margin=dict(l=10, r=10, t=72, b=0),
        hovermode="x unified",
    )
    if chart_title:
        layout_updates["margin"] = dict(l=10, r=10, t=96, b=0)
        layout_updates["title"] = dict(
            text=chart_title, x=0.02, xanchor="left", font=dict(size=13)
        )
    fig.update_layout(**layout_updates)
    fig.update_xaxes(
        rangeslider=dict(visible=False),
    )
    return fig


# 条件标记：与「指标选择」label 一致；col 为 plot_df 列名；阈值为该列「绑图原值」量纲（% 序列为 0～100 动态百分位）
_PRICE_COLS = frozenset({"BTC价格", "4年均线", "200周均线"})

THRESHOLD_MARKER_SPECS: list[dict[str, object]] = [
    {
        "label": "RSI6%",
        "col": "__pct_RSI6",
        "low_k": "rsi6_pct_mark_low",
        "high_k": "rsi6_pct_mark_high",
        "d_lo": 2.0,
        "d_hi": 98.0,
        "vmin": 0.0,
        "vmax": 100.0,
        "step": 0.5,
        "mark_lo": "#78350f",
        "mark_hi": "#6d28d9",
        "mark_lo_cn": "棕",
        "mark_hi_cn": "紫",
    },
    {
        "label": "RSI12%",
        "col": "__pct_RSI12",
        "low_k": "thr_rsi12_pct_low",
        "high_k": "thr_rsi12_pct_high",
        "d_lo": 2.0,
        "d_hi": 98.0,
        "vmin": 0.0,
        "vmax": 100.0,
        "step": 0.5,
        "mark_lo": "#78350f",
        "mark_hi": "#6d28d9",
        "mark_lo_cn": "棕",
        "mark_hi_cn": "紫",
    },
    {
        "label": "恐惧贪婪%",
        "col": "__pct_恐惧贪婪",
        "low_k": "thr_fg_pct_low",
        "high_k": "thr_fg_pct_high",
        "d_lo": 2.0,
        "d_hi": 98.0,
        "vmin": 0.0,
        "vmax": 100.0,
        "step": 0.5,
        "mark_lo": "#ca8a04",
        "mark_hi": "#ea580c",
        "mark_lo_cn": "黄",
        "mark_hi_cn": "橙",
    },
    {
        "label": "Ahr999%",
        "col": "__pct_Ahr999",
        "low_k": "thr_ahr999_pct_low",
        "high_k": "thr_ahr999_pct_high",
        "d_lo": 2.0,
        "d_hi": 98.0,
        "vmin": 0.0,
        "vmax": 100.0,
        "step": 0.5,
        "mark_lo": "#c026d3",
        "mark_hi": "#4338ca",
        "mark_lo_cn": "品红",
        "mark_hi_cn": "靛蓝",
    },
    {
        "label": "RSI6",
        "col": "RSI6",
        "low_k": "thr_rsi6_raw_low",
        "high_k": "thr_rsi6_raw_high",
        "d_lo": 30.0,
        "d_hi": 70.0,
        "vmin": 0.0,
        "vmax": 100.0,
        "step": 1.0,
        "mark_lo": "#2563eb",
        "mark_hi": "#16a34a",
        "mark_lo_cn": "蓝",
        "mark_hi_cn": "绿",
    },
    {
        "label": "RSI12",
        "col": "RSI12",
        "low_k": "thr_rsi12_raw_low",
        "high_k": "thr_rsi12_raw_high",
        "d_lo": 30.0,
        "d_hi": 70.0,
        "vmin": 0.0,
        "vmax": 100.0,
        "step": 1.0,
        "mark_lo": "#2563eb",
        "mark_hi": "#16a34a",
        "mark_lo_cn": "蓝",
        "mark_hi_cn": "绿",
    },
    {
        "label": "恐惧贪婪",
        "col": "恐惧贪婪",
        "low_k": "thr_fg_raw_low",
        "high_k": "thr_fg_raw_high",
        "d_lo": 20.0,
        "d_hi": 80.0,
        "vmin": 0.0,
        "vmax": 100.0,
        "step": 1.0,
        "mark_lo": "#dc2626",
        "mark_hi": "#171717",
        "mark_lo_cn": "红",
        "mark_hi_cn": "黑",
    },
    {
        "label": "Ahr999",
        "col": "Ahr999",
        "low_k": "thr_ahr999_raw_low",
        "high_k": "thr_ahr999_raw_high",
        "d_lo": 0.45,
        "d_hi": 1.2,
        "vmin": 0.0,
        "vmax": 3.0,
        "step": 0.01,
        "mark_lo": "#0891b2",
        "mark_hi": "#64748b",
        "mark_lo_cn": "青",
        "mark_hi_cn": "灰",
    },
]


def _yaxis_for_series_col(col: str) -> str:
    """原值双轴图：价格主轴 y，其余 y2。"""
    return "y" if col in _PRICE_COLS else "y2"


def _add_series_threshold_markers(
    fig: go.Figure,
    plot_df: pd.DataFrame,
    selected_cols: list[str],
    col: str,
    *,
    series_title: str,
    normalized: bool,
    normalize_method: str,
    low_th: float,
    high_th: float,
    norm_df: pd.DataFrame | None,
    low_color: str,
    high_color: str,
    low_color_cn: str,
    high_color_cn: str,
) -> None:
    """在已绑定的 fig 上叠加：低于左阈值、高于右阈值的实心圆点（颜色由指标配置）。"""
    if col not in selected_cols or col not in plot_df.columns:
        return
    val = pd.to_numeric(plot_df[col], errors="coerce")
    if normalized:
        nd = norm_df
        if nd is None:
            if normalize_method == "区间归一化(0-100，推荐)":
                nd = _normalize_minmax_100(plot_df, selected_cols)
            else:
                nd = _normalize_base_100(plot_df, selected_cols)
        y_line = pd.to_numeric(nd[col], errors="coerce")
        yaxis = "y"
    else:
        y_line = val
        yaxis = _yaxis_for_series_col(col)

    mask_low = val.notna() & (val < float(low_th))
    mask_high = val.notna() & (val > float(high_th))
    ht_val = series_title.replace("%", "%%")

    if mask_low.any():
        fig.add_trace(
            go.Scatter(
                x=plot_df.loc[mask_low, "date"],
                y=y_line[mask_low],
                mode="markers",
                name=f"{series_title} < {low_th:g}（{low_color_cn}）",
                marker=dict(color=low_color, size=9, symbol="circle", line=dict(width=0)),
                yaxis=yaxis,
                showlegend=True,
                customdata=val[mask_low],
                hovertemplate=(
                    "日期: %{x|%Y-%m-%d}<br>"
                    f"{ht_val}: " "%{customdata:.4f}<extra></extra>"
                ),
            )
        )
    if mask_high.any():
        fig.add_trace(
            go.Scatter(
                x=plot_df.loc[mask_high, "date"],
                y=y_line[mask_high],
                mode="markers",
                name=f"{series_title} > {high_th:g}（{high_color_cn}）",
                marker=dict(color=high_color, size=9, symbol="circle", line=dict(width=0)),
                yaxis=yaxis,
                showlegend=True,
                customdata=val[mask_high],
                hovertemplate=(
                    "日期: %{x|%Y-%m-%d}<br>"
                    f"{ht_val}: " "%{customdata:.4f}<extra></extra>"
                ),
            )
        )


st.set_page_config(page_title="BTC- INDEX分析", layout="wide")
st.title("BTC- INDEX分析")
st.caption(
    "全历史约 1 小时缓存刷新；快捷时间与滑块仅切日期。"
    "带 % 的序列：当前区间内从首日至当日的动态 rank 百分位。"
)
# 压缩主区图表带默认块间距（非隐藏模块，实为 Streamlit 控件外层 padding/margin）
st.markdown(
    """
    <style>
    section.main div[data-testid='stMultiSelect'] { margin-bottom: -1.1rem !important; }
    section.main div[data-testid='stRadio'] { margin-bottom: 0.05rem !important; }
    /* 仅含 segmented_control 的横排：标签+快捷按钮 与上图之间 */
    section.main div[data-testid='stHorizontalBlock']:has([data-baseweb="button-group"]) {
        margin-top: -0.25rem !important;
        margin-bottom: -0.65rem !important;
    }
    section.main div[data-testid='stPlotlyChart'] {
        margin-top: -0.5rem !important;
        margin-bottom: -3.4rem !important;
    }
    section.main div[data-testid='stSlider'] {
        margin-top: -3rem !important;
        padding-top: 0 !important;
        margin-bottom: 0.25rem !important;
    }
    section.main .stPlotlyChart iframe { vertical-align: bottom !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

df_all = load_full_chart_dataframe()
if df_all.empty:
    st.warning("当前数据库没有 BTC 价格数据。请先完成初始化抓取。")
    st.stop()

# 默认视窗定位最近1年（但图中仍加载全历史，便于向前拖拽查看）
max_date = df_all["date"].max()
one_year_ago = max_date - pd.Timedelta(days=365)

# 日期序列与 session 默认值（控件本体放在走势图上方，逻辑不变）
all_dates = sorted(df_all["date"].dt.date.unique().tolist())
if "norm_window_range" not in st.session_state:
    st.session_state["norm_window_range"] = (one_year_ago.date(), max_date.date())
quick_options = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "4Y", "ALL"]
if "quick_window" not in st.session_state:
    st.session_state["quick_window"] = "1Y"
if "quick_window_applied" not in st.session_state:
    # 与当前 quick 一致，避免首屏 None != "1Y" 反复写回「快捷 1Y」区间、冲掉滑块/其它控件刚改的日期
    st.session_state["quick_window_applied"] = st.session_state.get("quick_window", "1Y")

_h1, _h2 = st.columns(2, gap="small")
with _h1:
    mode = st.radio(
        "图表展示模式",
        options=["原值双轴（价格+指标）", "归一化对比（推荐）"],
        index=1,
        horizontal=True,
        help="归一化：价格与均线等同量纲共用刻度，横比更直观。",
    )
with _h2:
    normalize_method = st.radio(
        "归一化算法",
        options=["区间归一化(0-100，推荐)", "起点归一化(首点=100)"],
        index=0,
        horizontal=True,
        help="区间归一化：同量纲组共用窗口高低点。",
    )

series_labels = _series_labels_available(df_all)
chosen = st.multiselect(
    "指标选择",
    options=series_labels,
    default=[x for x in ("BTC价格", "恐惧贪婪%") if x in series_labels],
    help="带 %：当前上图日期范围内扩展窗口百分位；其余为原值。",
)

if not chosen:
    st.info("请至少勾选一个指标。")
    st.stop()

# 展示选项 → 指标选择 → 快捷时间（单行）→ 图 → 日期滑块
_qt1, _qt2 = st.columns([1, 8], gap="small", vertical_alignment="center")
with _qt1:
    st.markdown(
        "<p style='margin:0;line-height:2.5rem;font-size:0.875rem;white-space:nowrap'>快捷时间</p>",
        unsafe_allow_html=True,
    )
with _qt2:
    quick_window = st.segmented_control(
        "",
        options=quick_options,
        default=st.session_state["quick_window"],
        key="quick_window",
        help="点击后自动更新日期区间与下方滑块，并联动图表。",
        label_visibility="hidden",
    )

_qtok = _as_quick_token(quick_window)
if _qtok and st.session_state["quick_window_applied"] != _qtok:
    end_d = max_date.date()
    if _qtok == "ALL":
        start_d = all_dates[0]
    elif _qtok.endswith("M"):
        months = int(_qtok[:-1])
        start_d = (max_date - pd.DateOffset(months=months)).date()
    else:
        years = int(_qtok[:-1])
        start_d = (max_date - pd.DateOffset(years=years)).date()
    if start_d < all_dates[0]:
        start_d = all_dates[0]
    st.session_state["norm_window_range"] = (start_d, end_d)
    st.session_state["quick_window_applied"] = _qtok

_default_win = (one_year_ago.date(), max_date.date())
_raw_win = st.session_state.get("norm_window_range")
# Streamlit 会把区间存成 list；其内部用 isinstance(..., tuple) 判断区间模式，list 会走错分支
if isinstance(_raw_win, list) and len(_raw_win) == 2:
    _raw_win = (
        pd.Timestamp(_raw_win[0]).date(),
        pd.Timestamp(_raw_win[1]).date(),
    )
    st.session_state["norm_window_range"] = _raw_win
_rng = _coerce_norm_window_range(_raw_win, all_dates, _default_win)
_canon = _canonicalize_slider_range(_rng[0], _rng[1], all_dates)
if _norm_window_needs_canonicalize(_raw_win, _canon):
    st.session_state["norm_window_range"] = _canon

# value 必须是含两个端点的序列：若 value=None，Streamlit 会把 default_indices 设为 [0]，
# SelectSliderSerde 按「单选」反序列化，区间 UI 与 session 会错乱并回退到约一年窗口。
st.select_slider(
    "",
    options=all_dates,
    value=_canon,
    key="norm_window_range",
    label_visibility="hidden",
)
_fin = st.session_state.get("norm_window_range")
start_d, end_d = _canonicalize_slider_range(
    *_coerce_norm_window_range(_fin, all_dates, _canon),
    all_dates,
)

df_window = df_all[(df_all["date"].dt.date >= start_d) & (df_all["date"].dt.date <= end_d)].copy()
if df_window.empty:
    df_window = df_all.copy()

plot_df, selected_cols, trace_display_names = _build_plot_dataframe(df_window, chosen)

if not selected_cols:
    st.warning("所选指标在当前日期范围内没有可用数据列。")
    st.stop()

if mode == "归一化对比（推荐）":
    fig = _build_figure_normalized(
        plot_df,
        selected_cols,
        normalize_method,
        trace_display_names=trace_display_names,
    )
else:
    fig = _build_figure(
        plot_df,
        selected_cols,
        trace_display_names=trace_display_names,
    )
    x0, x1 = plot_df["date"].min(), plot_df["date"].max()
    fig.update_xaxes(range=[x0, x1])

_marker_chosen = any(cfg["label"] in chosen for cfg in THRESHOLD_MARKER_SPECS)
_norm_for_markers: pd.DataFrame | None = None
if _marker_chosen and mode == "归一化对比（推荐）":
    if normalize_method == "区间归一化(0-100，推荐)":
        _norm_for_markers = _normalize_minmax_100(plot_df, selected_cols)
    else:
        _norm_for_markers = _normalize_base_100(plot_df, selected_cols)

for cfg in THRESHOLD_MARKER_SPECS:
    if cfg["label"] not in chosen:
        continue
    st.session_state.setdefault(str(cfg["low_k"]), float(cfg["d_lo"]))
    st.session_state.setdefault(str(cfg["high_k"]), float(cfg["d_hi"]))
    _add_series_threshold_markers(
        fig,
        plot_df,
        selected_cols,
        str(cfg["col"]),
        series_title=str(cfg["label"]),
        normalized=mode == "归一化对比（推荐）",
        normalize_method=normalize_method,
        low_th=float(st.session_state[str(cfg["low_k"])]),
        high_th=float(st.session_state[str(cfg["high_k"])]),
        norm_df=_norm_for_markers,
        low_color=str(cfg["mark_lo"]),
        high_color=str(cfg["mark_hi"]),
        low_color_cn=str(cfg["mark_lo_cn"]),
        high_color_cn=str(cfg["mark_hi_cn"]),
    )

st.plotly_chart(
    fig,
    use_container_width=True,
    config={"displayModeBar": False},
)

_marker_ui = False
for cfg in THRESHOLD_MARKER_SPECS:
    if cfg["label"] not in chosen:
        continue
    if not _marker_ui:
        st.caption(
            "条件标记：低于左阈值、高于右阈值以不同颜色圆点标示，各指标颜色不同（见图例）。"
            "阈值与图中该序列的绑图原值一致（带 % 的为 0～100 动态百分位；归一化模式下圆点贴在归一化后的曲线上）。"
        )
        _marker_ui = True
    st.markdown(f"**{cfg['label']}**")
    _m1, _m2 = st.columns(2, gap="small")
    with _m1:
        st.number_input(
            f"低于 → {cfg['mark_lo_cn']}点",
            min_value=float(cfg["vmin"]),
            max_value=float(cfg["vmax"]),
            step=float(cfg["step"]),
            key=str(cfg["low_k"]),
        )
    with _m2:
        st.number_input(
            f"高于 → {cfg['mark_hi_cn']}点",
            min_value=float(cfg["vmin"]),
            max_value=float(cfg["vmax"]),
            step=float(cfg["step"]),
            key=str(cfg["high_k"]),
        )

st.subheader("指标说明表")
_rv_cols = st.columns([1, 1, 8], gap="small")
with _rv_cols[0]:
    _window = st.selectbox("实时窗口", options=["2Y"], index=0, key="rt_window", label_visibility="collapsed")
with _rv_cols[1]:
    if st.button("刷新当前值", use_container_width=True):
        _load_realtime_factor_scores.clear()
        st.rerun()

realtime_map = get_realtime_values(window=_window)


@st.cache_data(ttl=120, show_spinner=False)
def _load_realtime_factor_scores() -> tuple[dict[str, int], int, int]:
    """基于 2Y 实时输入计算各因子得分、归一化总分与建议仓位。"""
    inp = build_inputs_realtime()
    scores = get_factor_scores(inputs=inp)
    meta = calculate_normalized_score_and_position(inputs=inp)
    return scores, meta["normalized_score"], meta["suggested_position"]


def _fmt_factor_cell(scores: dict[str, int], *codes: str) -> str:
    """取指定因子中非 0 的得分，用于表格单元格展示。"""
    parts = [str(scores[c]) for c in codes if scores.get(c, 0) != 0]
    return ", ".join(parts) if parts else "0"


def _style_score_column(series: pd.Series) -> list[str]:
    styles: list[str] = []
    for i, val in enumerate(series):
        text = str(val).strip()
        is_total_row = i == len(series) - 1
        weight = "font-weight: 700; " if is_total_row else ""
        if text in {"", "-", "—"}:
            styles.append(f"{weight}color: #95a5a6")
            continue
        m = re.search(r"-?\d+", text)
        n = int(m.group()) if m else 0
        if n < 0:
            styles.append(f"{weight}color: #e74c3c")
        elif n > 0:
            styles.append(f"{weight}color: #27ae60")
        else:
            styles.append(f"{weight}color: #95a5a6")
    return styles


def _fmt_current(indicator_code: str) -> str:
    row = realtime_map.get(indicator_code)
    if not row:
        return "-"
    v = row.get("current_value")
    if v is None:
        return "-"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return "-"
    # 百分位类按 0~100 展示两位；其余按四位小数
    if indicator_code in {"rsi6_pct_ui", "rsi12_pct_ui", "fg_pct_ui", "ahr999_pct_ui"}:
        return f"{fv:.2f}"
    return f"{fv:.4f}"


if _window == "2Y":
    _factor_scores, _total_score, _suggested_position = _load_realtime_factor_scores()
else:
    _factor_scores = {k: 0 for k in ("1A", "2A", "2B", "3A", "3B", "4A", "4B", "5A", "5B", "5C", "6A", "6B")}
    _total_score = 0
    _suggested_position = 0

_score_empty = "—" if _window != "2Y" else "0"
_fg_score = _fmt_factor_cell(_factor_scores, "4A", "4B") if _window == "2Y" else _score_empty

info_rows = [
    {
        "指标代码": "close",
        "中文名称": "BTC价格",
        "当前值": _fmt_current("close"),
        "评分": _fmt_factor_cell(_factor_scores, "1A") if _window == "2Y" else _score_empty,
        "指标解释": "比特币当日收盘价（USD）",
    },
    {
        "指标代码": "rsi6",
        "中文名称": "RSI6",
        "当前值": _fmt_current("rsi6"),
        "评分": _score_empty,
        "指标解释": "6日相对强弱指标，反映短周期动量",
    },
    {
        "指标代码": "rsi12",
        "中文名称": "RSI12",
        "当前值": _fmt_current("rsi12"),
        "评分": _score_empty,
        "指标解释": "12日相对强弱指标，反映中短周期动量",
    },
    {
        "指标代码": "rsi6_pct_ui",
        "中文名称": "RSI6%（2Y）",
        "当前值": _fmt_current("rsi6_pct_ui"),
        "评分": _fmt_factor_cell(_factor_scores, "2A", "2B") if _window == "2Y" else _score_empty,
        "指标解释": "最近2年窗口（730天）按当前 RSI6 动态计算的百分位（0～100）",
    },
    {
        "指标代码": "rsi12_pct_ui",
        "中文名称": "RSI12%（2Y）",
        "当前值": _fmt_current("rsi12_pct_ui"),
        "评分": _fmt_factor_cell(_factor_scores, "3A", "3B") if _window == "2Y" else _score_empty,
        "指标解释": "最近2年窗口（730天）按当前 RSI12 动态计算的百分位（0～100）",
    },
    {
        "指标代码": "value",
        "中文名称": "恐惧贪婪",
        "当前值": _fmt_current("value"),
        "评分": _fg_score,
        "指标解释": "恐惧贪婪指数原值（0-100）",
    },
    {
        "指标代码": "fg_pct_ui",
        "中文名称": "恐惧贪婪%（2Y）",
        "当前值": _fmt_current("fg_pct_ui"),
        "评分": _fg_score,
        "指标解释": "最近2年窗口（730天）按当前恐惧贪婪值动态计算的百分位（0～100）",
    },
    {
        "指标代码": "ahr999_value",
        "中文名称": "Ahr999",
        "当前值": _fmt_current("ahr999_value"),
        "评分": _score_empty,
        "指标解释": "Ahr999估值指标（低于0.45偏低估，高于1.2偏高估）",
    },
    {
        "指标代码": "ahr999_pct_ui",
        "中文名称": "Ahr999%（2Y）",
        "当前值": _fmt_current("ahr999_pct_ui"),
        "评分": _fmt_factor_cell(_factor_scores, "5A", "5B", "5C") if _window == "2Y" else _score_empty,
        "指标解释": "最近2年窗口（730天）按当前 Ahr999 动态计算的百分位（0～100）",
    },
    {
        "指标代码": "ma_value(4y)",
        "中文名称": "4年均线",
        "当前值": _fmt_current("ma_value(4y)"),
        "评分": _score_empty,
        "指标解释": "1458日简单移动平均线",
    },
    {
        "指标代码": "price_to_4y_ma",
        "中文名称": "价格/4年均线",
        "当前值": _fmt_current("price_to_4y_ma"),
        "评分": _fmt_factor_cell(_factor_scores, "6A", "6B") if _window == "2Y" else _score_empty,
        "指标解释": "当前价格相对4年均线的倍数",
    },
    {
        "指标代码": "ma_value(200w)",
        "中文名称": "200周均线",
        "当前值": _fmt_current("ma_value(200w)"),
        "评分": _score_empty,
        "指标解释": "1400日简单移动平均线",
    },
    {
        "指标代码": "price_to_200w_ma",
        "中文名称": "价格/200周均线",
        "当前值": _fmt_current("price_to_200w_ma"),
        "评分": _score_empty,
        "指标解释": "当前价格相对200周均线的倍数",
    },
    {
        "指标代码": "—",
        "中文名称": "总分",
        "当前值": "—",
        "评分": (
            f"{_total_score}（建议仓位 {_suggested_position}%）"
            if _window == "2Y"
            else _score_empty
        ),
        "指标解释": "归一化总分 0~100（原始扣分按 60 分满额折算；2Y 实时）",
    },
]

_info_df = pd.DataFrame(info_rows)
_styled_info = _info_df.style.apply(_style_score_column, subset=["评分"])
st.dataframe(_styled_info, use_container_width=True, hide_index=True)

st.subheader("最新数据预览")
st.dataframe(df_all.tail(20), use_container_width=True)
