"""
恐惧贪婪指数抓取脚本。

本脚本负责：
1) 从 Alternative.me 官方 API 获取恐惧贪婪指数历史数据
2) 把 timestamp 转换为 YYYY-MM-DD 日期字符串
3) 通过 upsert 写入 btc_fear_greed 表（仅 value / classification；百分位由前端内存计算）

说明：
- API 仍拉全量；入库只写原始值
"""

from __future__ import annotations

import json
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import urlopen

import pandas as pd
from sqlalchemy import func, select

from app.db.database import SessionLocal, init_db, upsert_by_date
from app.db.models import BtcFearGreed

FNG_API_URL = "https://api.alternative.me/fng/?limit=0&format=json"


def _request_fng_with_retry() -> dict[str, Any]:
    """
    请求 FNG API，失败自动重试 3 次，每次等待 5 秒。
    """
    max_retries = 3
    ssl_context = ssl.create_default_context()
    insecure_ssl_context = ssl._create_unverified_context()
    for attempt in range(1, max_retries + 1):
        try:
            with urlopen(FNG_API_URL, timeout=30, context=ssl_context) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP 状态码异常: {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
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
    """把 Unix 时间戳转换为 YYYY-MM-DD（UTC）。"""
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def load_fng_history_df() -> pd.DataFrame:
    """拉取 API 全量历史，并整理为 DataFrame。"""
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
    return df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def _save_rows(df: pd.DataFrame) -> int:
    """把 DataFrame 全部行 upsert 到 btc_fear_greed。"""
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
                },
            )
            count += 1
        session.commit()
    return count


def fetch_full_history() -> int:
    """全量更新：从 API 拉全部历史并 upsert。"""
    init_db()
    print("[开始] 全量抓取恐惧贪婪历史数据...")
    base_df = load_fng_history_df()
    written = _save_rows(base_df)
    print(f"[完成] 全量写入完成，共处理 {written} 条")
    return written


def fetch_incremental() -> int:
    """
    增量更新（API 仍拉全量，对所有日期 upsert 原始值）。
    """
    init_db()
    print("[开始] 增量更新恐惧贪婪指数...")

    with SessionLocal() as session:
        latest_date = session.scalar(select(func.max(BtcFearGreed.date)))

    base_df = load_fng_history_df()
    if base_df.empty:
        print("[完成] API 没有返回数据。")
        return 0

    written = _save_rows(base_df)

    api_max = str(base_df["date"].max())
    if latest_date is None:
        print(f"[提示] 数据库原无历史；本次全量 upsert 共 {written} 条（API 最新日期 {api_max}）。")
    elif api_max > latest_date:
        print(f"[完成] 已 upsert {written} 条；API 最新日期 {api_max}（数据库原最新：{latest_date}）。")
    else:
        print(f"[完成] API 日期未推进（{api_max}），已对全部 {written} 条刷新原始值。")
    return written


def summarize_table() -> dict[str, Any]:
    """读取 btc_fear_greed 表统计信息 + 最新一条记录。"""
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
