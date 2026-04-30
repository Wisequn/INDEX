"""
数据抓取模块。

为了让结构清晰，这里拆成 3 个函数：
- fetch_crypto_history: 抓 BTC 等加密货币（yfinance）
- fetch_us_history: 抓美股（yfinance）
- fetch_cn_history: 抓 A 股（akshare）

统一返回 pandas.DataFrame，列名尽量统一为：
timestamp, open, high, low, close, volume
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

try:
    import akshare as ak
except ImportError:
    ak = None


def _normalize_yf_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    把 yfinance 返回的数据标准化。
    """
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    # yfinance 返回的索引是时间，先 reset_index 变成普通列
    data = df.reset_index().copy()
    data.columns = [str(col).lower() for col in data.columns]

    # 有些场景列名是 date，有些是 datetime，这里统一成 timestamp
    if "datetime" in data.columns:
        data = data.rename(columns={"datetime": "timestamp"})
    elif "date" in data.columns:
        data = data.rename(columns={"date": "timestamp"})

    # 统一输出列
    keep_cols = ["timestamp", "open", "high", "low", "close", "volume"]
    for col in keep_cols:
        if col not in data.columns:
            data[col] = None

    return data[keep_cols]


def fetch_crypto_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    抓取加密货币历史数据（例：BTC-USD）。
    """
    raw = yf.download(symbol, start=start, end=end, interval="1d", progress=False)
    return _normalize_yf_dataframe(raw)


def fetch_us_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    抓取美股历史数据（例：AAPL）。
    """
    raw = yf.download(symbol, start=start, end=end, interval="1d", progress=False)
    return _normalize_yf_dataframe(raw)


def fetch_cn_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    抓取 A 股历史数据（例：000001.SZ）。

    说明：
    - akshare 的接口较多，这里用 stock_zh_a_hist 做演示
    - 传入 symbol 只保留 6 位数字（如 000001）
    """
    if ak is None:
        raise ImportError("akshare 未安装，请先执行 pip install akshare")

    pure_symbol = symbol.split(".")[0]
    start_date = start.replace("-", "")
    end_date = end.replace("-", "")

    raw = ak.stock_zh_a_hist(
        symbol=pure_symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )

    if raw.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    # akshare 的中文列名映射为统一英文列名
    data = raw.rename(
        columns={
            "日期": "timestamp",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
        }
    ).copy()

    keep_cols = ["timestamp", "open", "high", "low", "close", "volume"]
    for col in keep_cols:
        if col not in data.columns:
            data[col] = None

    return data[keep_cols]
