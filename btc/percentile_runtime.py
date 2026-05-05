"""
滚动历史百分位（仅内存 / 实时计算用，不再写入数据库）。

语义（与旧库中预计算列一致）：
- 「当天值」在「含当天、向前数 N 个日历日」的样本里的排名百分位（0~100）
- 算法：scipy.stats.percentileofscore(..., kind=\"rank\")

Streamlit 在页面打开时用全历史 DataFrame 算一次并缓存；切换日期窗口只切片，不重算。
"""

from __future__ import annotations

from typing import Final

import pandas as pd
from scipy.stats import percentileofscore

# 各「年」标签对应的滚动窗口天数（日历日）
ROLLING_PERCENTILE_YEAR_DAYS: Final[dict[int, int]] = {
    1: 365,
    2: 730,
    3: 1095,
    4: 1460,
}


def rolling_percentile_series(values: pd.Series, window_days: int | None) -> pd.Series:
    """
    对整列逐日计算滚动百分位，返回与 values 等长的 Series。
    """
    result = pd.Series(index=values.index, dtype="float64")

    for i in range(len(values)):
        current_value = values.iloc[i]
        if pd.isna(current_value):
            continue

        if window_days is None:
            window = values.iloc[: i + 1]
        else:
            start = max(0, i - window_days + 1)
            window = values.iloc[start : i + 1]

        window = window.dropna()
        if window.empty:
            continue

        result.iloc[i] = float(percentileofscore(window.to_numpy(), current_value, kind="rank"))

    return result


def calculate_dynamic_percentile(
    window_values: pd.Series,
    current_value: float,
) -> float | None:
    """
    给定「已截好的历史窗口样本」与当前值，返回 rank 百分位（0~100）。

    参数：
    - window_values: 含当天在内的窗口内指标序列（已按时间升序对齐）
    - current_value: 通常等于窗口最后一个有效值（当天）

    用于单点计算（如 realtime）或单元测试；整表仍优先用 rolling_percentile_series。
    """
    if pd.isna(current_value):
        return None
    w = window_values.dropna()
    if w.empty:
        return None
    arr = w.to_numpy()
    return float(percentileofscore(arr, current_value, kind="rank"))


def add_multi_year_percentiles(
    df: pd.DataFrame,
    value_col: str,
    name_prefix: str,
    years: tuple[int, ...] = (1, 2, 3, 4),
) -> pd.DataFrame:
    """
    就地增加列：{name_prefix}_pct_{y}y；不写入数据库。
    """
    for y in years:
        days = ROLLING_PERCENTILE_YEAR_DAYS[y]
        df[f"{name_prefix}_pct_{y}y"] = rolling_percentile_series(df[value_col], days)
    return df


def build_eod_percentile_api_dict(values: pd.Series, key_stem: str) -> dict[str, float | None]:
    """
    取序列「最后一个有效点」的多窗口百分位，键名与 btc/realtime 旧版一致。

    key_stem 为 \"rsi6\" / \"rsi12\" / \"fg\" / \"ahr999\"：
    - 生成例如 rsi6_pct_1y_eod … rsi6_pct_all_eod
    """
    s = pd.to_numeric(values, errors="coerce").reset_index(drop=True)
    valid_positions = [j for j in range(len(s)) if pd.notna(s.iloc[j])]
    if not valid_positions:
        return {}

    i = valid_positions[-1]
    current = float(s.iloc[i])
    out: dict[str, float | None] = {}

    for y in (1, 2, 3, 4):
        days = ROLLING_PERCENTILE_YEAR_DAYS[y]
        start = max(0, i - days + 1)
        window = s.iloc[start : i + 1]
        out[f"{key_stem}_pct_{y}y_eod"] = calculate_dynamic_percentile(window, current)

    out[f"{key_stem}_pct_all_eod"] = calculate_dynamic_percentile(s.iloc[: i + 1], current)
    return out
