"""
Streamlit 图表用：全历史一次加载 + 内存中动态百分位（不写库）。

- `load_full_chart_dataframe` 使用 @st.cache_data(ttl=3600)，整页共享一份大表。
- 百分位列与旧版列名一致（RSI6-1Y%、恐惧贪婪-2Y% 等），便于 main.py 多选逻辑不变。
- 用户拖动「归一化时间窗口」时只做 pandas 切片，不重算百分位，保证 <50ms。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.db.models import Btc200wMa, Btc4yMa, BtcAhr999, BtcFearGreed, BtcPrice, BtcRsi
from btc.percentile_runtime import add_multi_year_percentiles, rolling_percentile_series


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


def _add_dynamic_percentile_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    在全历史表上就地增加 RSI / 恐惧贪婪 / Ahr999 的 1Y~4Y+ALL 百分位列（展示用列名与旧版一致）。
    """
    if df.empty:
        return df

    out = df.copy()

    # RSI6
    s6 = pd.to_numeric(out["RSI6"], errors="coerce")
    w6 = pd.DataFrame({"RSI6": s6})
    add_multi_year_percentiles(w6, "RSI6", "rsi6", years=(1, 2, 3, 4))
    w6["rsi6_pct_all"] = rolling_percentile_series(s6, None)
    for y in (1, 2, 3, 4):
        out[f"RSI6-{y}Y%"] = w6[f"rsi6_pct_{y}y"]
    out["RSI6-ALL%"] = w6["rsi6_pct_all"]

    # RSI12
    s12 = pd.to_numeric(out["RSI12"], errors="coerce")
    w12 = pd.DataFrame({"RSI12": s12})
    add_multi_year_percentiles(w12, "RSI12", "rsi12", years=(1, 2, 3, 4))
    w12["rsi12_pct_all"] = rolling_percentile_series(s12, None)
    for y in (1, 2, 3, 4):
        out[f"RSI12-{y}Y%"] = w12[f"rsi12_pct_{y}y"]
    out["RSI12-ALL%"] = w12["rsi12_pct_all"]

    # 恐惧贪婪（原值列名：恐惧贪婪）
    fg = pd.to_numeric(out["恐惧贪婪"], errors="coerce")
    wf = pd.DataFrame({"恐惧贪婪": fg})
    add_multi_year_percentiles(wf, "恐惧贪婪", "fg", years=(1, 2, 3, 4))
    wf["fg_pct_all"] = rolling_percentile_series(fg, None)
    for y in (1, 2, 3, 4):
        out[f"恐惧贪婪-{y}Y%"] = wf[f"fg_pct_{y}y"]
    out["恐惧贪婪-ALL%"] = wf["fg_pct_all"]

    # Ahr999
    av = pd.to_numeric(out["Ahr999"], errors="coerce")
    wa = pd.DataFrame({"Ahr999": av})
    add_multi_year_percentiles(wa, "Ahr999", "ahr999", years=(1, 2, 3, 4))
    wa["ahr999_pct_all"] = rolling_percentile_series(av, None)
    for y in (1, 2, 3, 4):
        out[f"Ahr999-{y}Y%"] = wa[f"ahr999_pct_{y}y"]
    out["Ahr999-ALL%"] = wa["ahr999_pct_all"]

    return out


@st.cache_data(ttl=3600, show_spinner="正在加载全历史到内存并计算百分位（每小时刷新一次缓存）…")
def load_full_chart_dataframe() -> pd.DataFrame:
    """
    页面级缓存：一次 SQL 合并 + 一次全表百分位（与旧预计算语义一致）。

    说明：
    - 切换日期窗口时不要调用本函数，只对返回的 DataFrame 做布尔切片即可。
    - 需要强制刷新时可 st.cache_data.clear() 或调低 ttl。
    """
    init_db()
    raw = _load_raw_merged_from_db()
    return _add_dynamic_percentile_display_columns(raw)
