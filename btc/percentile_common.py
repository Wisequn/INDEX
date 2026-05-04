"""
多窗口「滚动历史百分位」公共计算（与 RSI / 恐惧贪婪 / Ahr999 现有 *_pct_1y 语义一致）。

说明（大白话）：
- 这里的「百分位」不是涨跌幅 `pct_change`，而是：「当天指标值」在「过去 N 个日历日（含当天）」
  这段历史里排第百分之几（0~100），算法与原来一致：`scipy.stats.percentileofscore(..., kind='rank')`。
- 窗口天数约定：1Y=365，2Y=730，3Y=1095，4Y=1460（与项目里原有 4Y 窗口一致）。
"""

from __future__ import annotations

from typing import Final

import pandas as pd
from scipy.stats import percentileofscore

# 各「年」标签对应的滚动窗口天数（日历日，非交易日）
ROLLING_PERCENTILE_YEAR_DAYS: Final[dict[int, int]] = {
    1: 365,
    2: 730,
    3: 1095,
    4: 1460,
}


def rolling_percentile_series(values: pd.Series, window_days: int | None) -> pd.Series:
    """
    计算序列中每个点在窗口内的历史百分位（0~100）。

    参数：
    - values: 指标序列（如 RSI6），须与日线索引对齐
    - window_days:
        - 365 / 730 / 1095 / 1460 等：表示「含当天」向前数这么多天的子序列
        - None：从序列开头到当天（全历史）

    返回：
    - 与 values 等长的 Series，无法计算的位置为 NaN
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


def add_multi_year_percentiles(
    df: pd.DataFrame,
    value_col: str,
    name_prefix: str,
    years: tuple[int, ...] = (1, 2, 3, 4),
) -> pd.DataFrame:
    """
    在 DataFrame 上就地增加多列「{name_prefix}_pct_{y}y」滚动百分位。

    示例：name_prefix=\"rsi6\"、years=(1,2,3,4) 会生成 rsi6_pct_1y … rsi6_pct_4y。

    参数：
    - df: 必须包含 value_col，且已按日期升序
    - value_col: 要算百分位的列名
    - name_prefix: 生成列名前缀（不含 _pct_）
    - years: 需要计算的年份标签集合

    返回：
    - 同一份 df（便于链式调用）
    """
    for y in years:
        days = ROLLING_PERCENTILE_YEAR_DAYS[y]
        df[f"{name_prefix}_pct_{y}y"] = rolling_percentile_series(df[value_col], days)
    return df
