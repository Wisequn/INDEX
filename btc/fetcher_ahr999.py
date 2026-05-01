"""
BTC Ahr999 历史数据抓取与计算脚本（Prompt 6）。

按优先级自动尝试（与是否设置 CG_API_KEY 有关）：

- **未设置 CG_API_KEY**：不请求 CoinGlass、不请求公开第三方，**直接本地计算**（幂律回归 + 365 天几何均值），避免无 Key 时仍长时间等待易超时的公开接口。

- **已设置 CG_API_KEY**：1) CoinGlass 官方 API → 2) 失败则公开第三方 → 3) 再失败则本地计算。

最终把结果写入 btc_ahr999 表，写入方式为 upsert（按 date 去重更新）。
"""

from __future__ import annotations

import json
import os
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import gmean, percentileofscore
from sqlalchemy import func, select

from app.db.database import SessionLocal, init_db, upsert_by_date
from app.db.models import BtcAhr999

# 目标时间范围：按你的要求，从 2015 年开始
START_DATE = "2015-01-01"

# BTC 创世块时间（UTC）：2009-01-03
GENESIS_DATE = datetime(2009, 1, 3, tzinfo=timezone.utc)

# 数据源 1：CoinGlass 官方 AHR999 API（需要 API Key）
COINGLASS_AHR999_URL = "https://open-api-v4.coinglass.com/api/index/ahr999"

# 数据源 2：公开第三方接口（网页抓包发现）
PUBLIC_AHR999_URL = "https://api.btc123.fans/coinglass.php?leibie=ahr999"


def _url_get_json_with_retry(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_sec: int = 30,
    retries: int = 3,
) -> Any:
    """
    通用 GET JSON 请求函数：
    - 自动重试 3 次
    - 每次失败等待 5 秒
    - 对证书问题做兼容回退（仅在证书报错时启用）
    """
    ssl_context = ssl.create_default_context()
    insecure_ssl_context = ssl._create_unverified_context()
    req = Request(url, headers=headers or {})

    for attempt in range(1, retries + 1):
        try:
            with urlopen(req, timeout=timeout_sec, context=ssl_context) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP 状态码异常: {resp.status}")
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                try:
                    print("[提示] 检测到证书校验失败，自动切换到兼容模式重试...")
                    with urlopen(req, timeout=timeout_sec, context=insecure_ssl_context) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP 状态码异常: {resp.status}")
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as inner_exc:
                    if attempt == retries:
                        raise RuntimeError(f"请求失败，已重试 {retries} 次: {inner_exc}") from inner_exc
                    print(f"[重试] 第 {attempt} 次请求失败：{inner_exc}，5 秒后重试...")
                    time.sleep(5)
                    continue

            if attempt == retries:
                raise RuntimeError(f"请求失败，已重试 {retries} 次: {exc}") from exc
            print(f"[重试] 第 {attempt} 次请求失败：{exc}，5 秒后重试...")
            time.sleep(5)

    return None


def _compute_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算 Ahr999 的 1y / 4y / all 百分位。
    百分位结果按你的要求保留两位小数。
    """
    out = df.copy()
    values = out["ahr999_value"]

    def rolling_pct(window_days: int | None) -> pd.Series:
        result = pd.Series(index=values.index, dtype="float64")
        for i in range(len(values)):
            v = values.iloc[i]
            if pd.isna(v):
                continue
            if window_days is None:
                w = values.iloc[: i + 1]
            else:
                start = max(0, i - window_days + 1)
                w = values.iloc[start : i + 1]
            w = w.dropna()
            if w.empty:
                continue
            result.iloc[i] = percentileofscore(w.to_numpy(), v, kind="rank")
        return result

    out["ahr999_pct_1y"] = rolling_pct(365).round(2)
    out["ahr999_pct_4y"] = rolling_pct(1460).round(2)
    out["ahr999_pct_all"] = rolling_pct(None).round(2)
    return out


def _try_fetch_from_coinglass() -> pd.DataFrame | None:
    """
    第一优先：CoinGlass 官方 API。

    注意：需要环境变量 CG_API_KEY。
    """
    api_key = os.getenv("CG_API_KEY", "").strip()
    if not api_key:
        print("[数据源1] CoinGlass 官方 API 跳过：未设置 CG_API_KEY。")
        return None

    print("[数据源1] 尝试 CoinGlass 官方 API...")
    payload = _url_get_json_with_retry(
        COINGLASS_AHR999_URL,
        headers={"accept": "application/json", "CG-API-KEY": api_key},
        timeout_sec=30,
    )

    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        print(f"[数据源1] 返回异常：{payload}")
        return None

    data = payload.get("data", [])
    if not data:
        return None

    rows = []
    for item in data:
        date_str = str(item.get("date_string", "")).replace("/", "-")
        rows.append(
            {
                "date": date_str,
                "ahr999_value": float(item.get("ahr999_value")),
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    df = df[df["date"] >= START_DATE].reset_index(drop=True)
    return df if not df.empty else None


def _try_fetch_from_public_source() -> pd.DataFrame | None:
    """
    第二优先：公开第三方接口（无需 key，但稳定性不保证）。
    """
    print("[数据源2] 尝试公开第三方接口...")
    try:
        payload = _url_get_json_with_retry(PUBLIC_AHR999_URL, timeout_sec=20)
    except Exception as exc:
        print(f"[数据源2] 请求失败：{exc}")
        return None

    # 该接口可能返回不同结构，这里兼容常见格式：
    # 1) {"data":[{"date":"YYYY-MM-DD","ahr999":...}, ...]}
    # 2) {"data":{"timeList":[],"ahr999":[]}}
    if not isinstance(payload, dict):
        print("[数据源2] 返回不是 JSON 对象。")
        return None

    rows: list[dict[str, Any]] = []
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            d = item.get("date") or item.get("date_string")
            v = item.get("ahr999") if item.get("ahr999") is not None else item.get("ahr999_value")
            if d is None or v is None:
                continue
            rows.append({"date": str(d).replace("/", "-"), "ahr999_value": float(v)})
    elif isinstance(data, dict):
        time_list = data.get("timeList", [])
        ahr_list = data.get("ahr999", [])
        for ts, v in zip(time_list, ahr_list):
            try:
                # timeList 通常是 Unix 秒时间戳
                date_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
                rows.append({"date": date_str, "ahr999_value": float(v)})
            except Exception:
                continue

    if not rows:
        print("[数据源2] 未解析到有效历史数据。")
        return None

    df = pd.DataFrame(rows)
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    df = df[df["date"] >= START_DATE].reset_index(drop=True)
    return df if not df.empty else None


def _compute_from_price() -> pd.DataFrame:
    """
    第三优先：自计算 Ahr999。

    计算逻辑（按你的阈值体系）：
    Ahr999 = BTC现价^2 / (过去365天价格几何平均值 * 指数增长模型预测价格)

    指数增长模型：
    - 先算每一天距离创世块的天数 day_index
    - 用幂律回归拟合：log10(price) = a * log10(day_index) + b
    - 预测价格 model_price = 10 ** (a*log10(day_index) + b)
    """
    print("[数据源3] 使用本地自计算方式生成 Ahr999（幂律回归 + 365天几何均值）...")

    # 用 yfinance 拉 BTC-USD，从而覆盖到 2015（本地 btc_price 只有 2018 起）
    # 注意：yfinance 失败时会返回空表，这里做显式检查。
    hist = yf.download("BTC-USD", start=START_DATE, interval="1d", progress=False)
    if hist.empty:
        raise RuntimeError("yfinance 未返回 BTC 历史数据，无法进行 Ahr999 自计算。")

    # yfinance 可能是多级列，这里统一提取 close
    close_col = ("Close", "BTC-USD") if ("Close", "BTC-USD") in hist.columns else "Close"
    close_df = hist[[close_col]].copy()
    close_df.columns = ["close"]
    close_df = close_df.reset_index()
    close_df["date"] = close_df["Date"].dt.strftime("%Y-%m-%d")
    close_df = close_df[["date", "close"]].dropna().reset_index(drop=True)

    # day_index：距离创世块天数（最小为1，避免 log10(0)）
    date_dt = pd.to_datetime(close_df["date"], utc=True)
    day_index = (date_dt - GENESIS_DATE).dt.days.clip(lower=1).astype(float)
    close_df["day_index"] = day_index

    # 幂律拟合：log10(close) = a*log10(day_index)+b
    x = np.log10(close_df["day_index"].to_numpy())
    y = np.log10(close_df["close"].to_numpy())
    a, b = np.polyfit(x, y, 1)

    # 预测价格
    close_df["model_price"] = np.power(10.0, a * np.log10(close_df["day_index"]) + b)

    # 365天几何平均：用 rolling apply(gmean)
    close_df["gmean_365"] = (
        close_df["close"]
        .rolling(window=365, min_periods=365)
        .apply(lambda arr: float(gmean(arr)), raw=True)
    )

    # Ahr999 公式（使用平方形式，与 0.45 / 1.2 阈值体系一致）
    close_df["ahr999_value"] = (close_df["close"] ** 2) / (close_df["gmean_365"] * close_df["model_price"])

    out = close_df[["date", "ahr999_value"]].copy()
    out = out.dropna(subset=["ahr999_value"]).reset_index(drop=True)
    return out


def build_ahr999_history() -> tuple[pd.DataFrame, str]:
    """
    构建 Ahr999 历史数据。
    返回：
    - DataFrame（含 date, ahr999_value, 三个百分位）
    - 数据来源说明字符串
    """
    api_key = os.getenv("CG_API_KEY", "").strip()

    if api_key:
        # 有 Key：先官方，再公开第三方，最后才本地（与旧版行为一致）
        df = _try_fetch_from_coinglass()
        if df is not None:
            return _compute_percentiles(df), "CoinGlass 官方 API"

        df = _try_fetch_from_public_source()
        if df is not None:
            return _compute_percentiles(df), "公开第三方 Ahr999 接口"
    else:
        # 无 Key：不再打公开接口（避免多次超时重试），直接进入本地计算
        print(
            "[Ahr999] 未设置 CG_API_KEY：跳过 CoinGlass 与公开第三方，直接使用本地公式（yfinance 价格 + 幂律回归 + 365 日几何均值）。"
        )

    df = _compute_from_price()
    return _compute_percentiles(df), "本地自计算（幂律回归 + 365天几何均值）"


def _save_to_db(df: pd.DataFrame) -> int:
    """
    把 DataFrame 结果 upsert 到 btc_ahr999。
    """
    if df.empty:
        return 0

    written = 0
    with SessionLocal() as session:
        for _, row in df.iterrows():
            upsert_by_date(
                session=session,
                model=BtcAhr999,
                row_data={
                    "date": row["date"],
                    "ahr999_value": float(row["ahr999_value"]),
                    "ahr999_pct_1y": float(row["ahr999_pct_1y"]) if pd.notna(row["ahr999_pct_1y"]) else None,
                    "ahr999_pct_4y": float(row["ahr999_pct_4y"]) if pd.notna(row["ahr999_pct_4y"]) else None,
                    "ahr999_pct_all": float(row["ahr999_pct_all"]) if pd.notna(row["ahr999_pct_all"]) else None,
                },
            )
            written += 1
        session.commit()
    return written


def fetch_full_history() -> tuple[int, str]:
    """
    全量更新：构建全历史数据并整体写入。
    """
    init_db()
    print("[开始] 全量更新 Ahr999 历史数据...")
    df, source_used = build_ahr999_history()
    written = _save_to_db(df)
    print(f"[完成] 全量写入完成，共处理 {written} 条。数据来源：{source_used}")
    return written, source_used


def fetch_incremental() -> tuple[int, str]:
    """
    增量更新：
    - 读取数据库最新日期
    - 重建完整序列（保证百分位一致）
    - 仅写入“最新日期之后”的新数据
    """
    init_db()
    with SessionLocal() as session:
        latest_date = session.scalar(select(func.max(BtcAhr999.date)))

    df, source_used = build_ahr999_history()
    if latest_date is None:
        written = _save_to_db(df)
        return written, source_used

    inc_df = df[df["date"] > latest_date].copy()
    if inc_df.empty:
        print(f"[完成] Ahr999 已是最新（数据库最新日期：{latest_date}）。")
        return 0, source_used

    written = _save_to_db(inc_df)
    print(f"[完成] 增量写入 {written} 条（数据库之前最新日期：{latest_date}）。")
    return written, source_used


def summarize_table() -> dict[str, Any]:
    """
    返回：
    - 总条数、最早/最新日期
    - 最新一天与前一天的 ahr999 和百分位
    """
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(BtcAhr999)) or 0
        min_date, max_date = session.execute(select(func.min(BtcAhr999.date), func.max(BtcAhr999.date))).one()

        latest_two = session.execute(
            select(BtcAhr999).order_by(BtcAhr999.date.desc()).limit(2)
        ).scalars().all()

    latest = latest_two[0] if len(latest_two) >= 1 else None
    prev = latest_two[1] if len(latest_two) >= 2 else None
    return {
        "total_rows": int(total),
        "earliest_date": min_date,
        "latest_date": max_date,
        "latest": latest,
        "previous": prev,
    }


if __name__ == "__main__":
    written, source = fetch_full_history()
    summary = summarize_table()

    print("\n[结果] Ahr999 入库完成")
    print(f"- 数据来源: {source}")
    print(f"- 本次写入条数: {written}")
    print(f"- 表总条数: {summary['total_rows']}")
    print(f"- 最早日期: {summary['earliest_date']}")
    print(f"- 最新日期: {summary['latest_date']}")

    latest = summary["latest"]
    prev = summary["previous"]
    if latest is not None:
        print("\n- 最新一天:")
        print(f"  date={latest.date}, ahr999_value={latest.ahr999_value}, pct_1y={latest.ahr999_pct_1y}, pct_4y={latest.ahr999_pct_4y}, pct_all={latest.ahr999_pct_all}")
    if prev is not None:
        print("- 前一天:")
        print(f"  date={prev.date}, ahr999_value={prev.ahr999_value}, pct_1y={prev.ahr999_pct_1y}, pct_4y={prev.ahr999_pct_4y}, pct_all={prev.ahr999_pct_all}")
