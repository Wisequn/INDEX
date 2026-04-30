"""
恐惧贪婪指数抓取脚本（Prompt 5）。

本脚本负责：
1) 从 Alternative.me 官方 API 获取恐惧贪婪指数历史数据
2) 把 timestamp 转换为 YYYY-MM-DD 日期字符串
3) 计算 1年 / 4年 / 全历史 百分位（percentileofscore, kind='rank'）
4) 通过 upsert 写入 btc_fear_greed 表
5) 提供全量和增量两种更新方式
"""

from __future__ import annotations

import json
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import urlopen

import pandas as pd
from scipy.stats import percentileofscore
from sqlalchemy import func, select

from app.db.database import SessionLocal, init_db, upsert_by_date
from app.db.models import BtcFearGreed

# 官方 API：limit=0 表示返回全部历史
FNG_API_URL = "https://api.alternative.me/fng/?limit=0&format=json"


def _request_fng_with_retry() -> dict[str, Any]:
    """
    请求 FNG API，失败自动重试 3 次，每次等待 5 秒。
    """
    max_retries = 3
    ssl_context = ssl.create_default_context()
    # 某些代理环境会注入自签名证书，导致证书校验失败。
    # 仅在确实遇到证书错误时，才回退到不校验证书模式。
    insecure_ssl_context = ssl._create_unverified_context()
    for attempt in range(1, max_retries + 1):
        try:
            with urlopen(FNG_API_URL, timeout=30, context=ssl_context) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP 状态码异常: {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            # urllib 常把 SSL 错误包装成 URLError，这里用字符串兜底判断。
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                try:
                    print("[提示] 检测到证书校验失败，自动切换到兼容模式重试...")
                    with urlopen(FNG_API_URL, timeout=30, context=insecure_ssl_context) as response:
                        if response.status != 200:
                            raise RuntimeError(f"HTTP 状态码异常: {response.status}")
                        return json.loads(response.read().decode("utf-8"))
                except Exception as inner_exc:
                    if attempt == max_retries:
                        raise RuntimeError(f"请求恐惧贪婪 API 失败，已重试 {max_retries} 次: {inner_exc}") from inner_exc
                    print(f"[重试] 第 {attempt} 次请求失败：{inner_exc}，5 秒后重试...")
                    time.sleep(5)
                    continue

            if attempt == max_retries:
                raise RuntimeError(f"请求恐惧贪婪 API 失败，已重试 {max_retries} 次: {exc}") from exc
            print(f"[重试] 第 {attempt} 次请求失败：{exc}，5 秒后重试...")
            time.sleep(5)

    return {}


def _ts_to_date(ts: str | int) -> str:
    """
    把 Unix 时间戳转换为 YYYY-MM-DD（UTC）。
    """
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def load_fng_history_df() -> pd.DataFrame:
    """
    拉取 API 全量历史，并整理为 DataFrame。
    """
    payload = _request_fng_with_retry()
    records = payload.get("data", [])
    if not records:
        return pd.DataFrame(columns=["date", "value", "classification"])

    rows = []
    for item in records:
        rows.append(
            {
                "date": _ts_to_date(item["timestamp"]),
                "value": float(item["value"]),
                "classification": item.get("value_classification"),
            }
        )

    df = pd.DataFrame(rows)
    # API 默认通常是倒序，这里统一改成日期升序，便于窗口计算
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return df


def _rolling_percentile(values: pd.Series, window_days: int | None) -> pd.Series:
    """
    计算每一天 value 在窗口中的百分位（0-100）。
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


def build_with_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """
    基于 value 计算三个百分位字段。
    """
    if df.empty:
        return pd.DataFrame(columns=["date", "value", "classification", "fg_pct_1y", "fg_pct_4y", "fg_pct_all"])

    out = df.copy()
    out["fg_pct_1y"] = _rolling_percentile(out["value"], window_days=365)
    out["fg_pct_4y"] = _rolling_percentile(out["value"], window_days=1460)
    out["fg_pct_all"] = _rolling_percentile(out["value"], window_days=None)
    return out


def _save_rows(df: pd.DataFrame) -> int:
    """
    把 DataFrame 全部行 upsert 到 btc_fear_greed。
    """
    if df.empty:
        return 0

    count = 0
    with SessionLocal() as session:
        for _, row in df.iterrows():
            upsert_by_date(
                session=session,
                model=BtcFearGreed,
                row_data={
                    "date": row["date"],
                    "value": int(row["value"]) if pd.notna(row["value"]) else None,
                    "classification": row["classification"],
                    "fg_pct_1y": float(row["fg_pct_1y"]) if pd.notna(row["fg_pct_1y"]) else None,
                    "fg_pct_4y": float(row["fg_pct_4y"]) if pd.notna(row["fg_pct_4y"]) else None,
                    "fg_pct_all": float(row["fg_pct_all"]) if pd.notna(row["fg_pct_all"]) else None,
                },
            )
            count += 1
        session.commit()
    return count


def fetch_full_history() -> int:
    """
    全量更新：从 API 拉全部历史，重算全部百分位并 upsert。
    """
    init_db()
    print("[开始] 全量抓取恐惧贪婪历史数据...")
    base_df = load_fng_history_df()
    full_df = build_with_percentiles(base_df)
    written = _save_rows(full_df)
    print(f"[完成] 全量写入完成，共处理 {written} 条")
    return written


def fetch_incremental() -> int:
    """
    增量更新：
    1) 读取数据库最新日期
    2) 从 API 拉全量（官方接口不支持按日期过滤）
    3) 仅把“最新日期之后”的新记录入库
    4) 但百分位需要依赖历史，所以会先在内存重算全部，再筛选新日期写入
    """
    init_db()
    print("[开始] 增量更新恐惧贪婪指数...")

    with SessionLocal() as session:
        latest_date = session.scalar(select(func.max(BtcFearGreed.date)))

    base_df = load_fng_history_df()
    if base_df.empty:
        print("[完成] API 没有返回数据。")
        return 0

    full_df = build_with_percentiles(base_df)

    if latest_date is None:
        print("[提示] 数据库暂无历史，自动执行全量写入。")
        return _save_rows(full_df)

    inc_df = full_df[full_df["date"] > latest_date].copy()
    if inc_df.empty:
        print(f"[完成] 数据已是最新，数据库最新日期：{latest_date}")
        return 0

    written = _save_rows(inc_df)
    print(f"[完成] 增量写入完成，新增 {written} 条（数据库之前最新日期：{latest_date}）")
    return written


def summarize_table() -> dict[str, Any]:
    """
    读取 btc_fear_greed 表统计信息 + 最新一条记录。
    """
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(BtcFearGreed)) or 0
        min_date, max_date = session.execute(select(func.min(BtcFearGreed.date), func.max(BtcFearGreed.date))).one()

        latest_row = session.execute(
            select(BtcFearGreed).where(BtcFearGreed.date == max_date)
        ).scalar_one_or_none()

    return {
        "total_rows": int(total),
        "earliest_date": min_date,
        "latest_date": max_date,
        "latest_value": latest_row.value if latest_row else None,
        "latest_classification": latest_row.classification if latest_row else None,
        "latest_fg_pct_1y": latest_row.fg_pct_1y if latest_row else None,
        "latest_fg_pct_4y": latest_row.fg_pct_4y if latest_row else None,
        "latest_fg_pct_all": latest_row.fg_pct_all if latest_row else None,
    }


if __name__ == "__main__":
    fetch_full_history()
    summary = summarize_table()
    print("\n[结果] 恐惧贪婪入库完成")
    print(f"- 总条数: {summary['total_rows']}")
    print(f"- 最早日期: {summary['earliest_date']}")
    print(f"- 最新日期: {summary['latest_date']}")
    print(f"- 最新 value: {summary['latest_value']}")
    print(f"- 最新 classification: {summary['latest_classification']}")
    print(f"- 最新 fg_pct_1y: {summary['latest_fg_pct_1y']}")
    print(f"- 最新 fg_pct_4y: {summary['latest_fg_pct_4y']}")
    print(f"- 最新 fg_pct_all: {summary['latest_fg_pct_all']}")
