"""
BTC 日线价格抓取脚本（币安 API）。

本文件提供两个核心函数：
1) fetch_full_history()：全量抓取（2018-01-01 到今天）
2) fetch_incremental()：先修补库内「首尾日期之间」的缺失日，再增量抓取尾部到新数据

主要特性：
- 币安每次最多返回 1000 条，内部自动分页循环抓取
- 使用 upsert_by_date 写入 btc_price，避免重复数据
- 网络失败自动重试 3 次，每次等待 5 秒
- 终端实时打印进度，显示当前抓取到的日期
"""

from __future__ import annotations

import json
import ssl
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from sqlalchemy import func, select

from app.db.database import SessionLocal, init_db, upsert_by_date
from app.db.models import BtcPrice

# 币安 K 线接口
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# 固定抓 BTC/USDT 日线
SYMBOL = "BTCUSDT"
INTERVAL = "1d"
LIMIT = 1000

# 全量起始日期：按你的要求，从 2018-01-01 开始
FULL_START_DATE = "2018-01-01"


def _to_ms(date_str: str) -> int:
    """
    把 YYYY-MM-DD 日期字符串转换成 UTC 毫秒时间戳。
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ms_to_date(ms: int) -> str:
    """
    把 UTC 毫秒时间戳转换成 YYYY-MM-DD 日期字符串。
    """
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _request_klines_with_retry(start_time_ms: int, end_time_ms: int) -> list[list[Any]]:
    """
    请求一页 K 线数据，失败自动重试 3 次。

    重试规则：
    - 最多 3 次
    - 每次失败等待 5 秒再重试
    """
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "startTime": start_time_ms,
        "endTime": end_time_ms,
        "limit": LIMIT,
    }
    url = f"{BINANCE_KLINES_URL}?{urlencode(params)}"

    max_retries = 3
    ssl_context = ssl.create_default_context()
    # 某些本地代理环境会注入自签名证书，导致证书校验失败。
    # 这里提供一个“回退上下文”，仅在确实遇到证书错误时使用。
    insecure_ssl_context = ssl._create_unverified_context()

    for attempt in range(1, max_retries + 1):
        try:
            with urlopen(url, timeout=30, context=ssl_context) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP 状态码异常: {response.status}")
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except Exception as exc:
            # 注意：urllib 常把 SSL 错误包装成 URLError，所以这里用字符串再兜底判断一次。
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                try:
                    print("[提示] 检测到证书校验失败，自动切换到兼容模式重试...")
                    with urlopen(url, timeout=30, context=insecure_ssl_context) as response:
                        if response.status != 200:
                            raise RuntimeError(f"HTTP 状态码异常: {response.status}")
                        payload = response.read().decode("utf-8")
                        return json.loads(payload)
                except Exception as inner_exc:
                    if attempt == max_retries:
                        raise RuntimeError(f"请求币安 API 失败，已重试 {max_retries} 次: {inner_exc}") from inner_exc
                    print(f"[重试] 第 {attempt} 次请求失败：{inner_exc}，5 秒后重试...")
                    time.sleep(5)
                    continue

            if attempt == max_retries:
                raise RuntimeError(f"请求币安 API 失败，已重试 {max_retries} 次: {exc}") from exc
            print(f"[重试] 第 {attempt} 次请求失败：{exc}，5 秒后重试...")
            time.sleep(5)

    return []


def _save_klines_to_db(klines: list[list[Any]]) -> int:
    """
    把一批 K 线写入数据库 btc_price 表，返回本批处理条数。
    """
    if not klines:
        return 0

    affected = 0
    with SessionLocal() as session:
        for item in klines:
            # 币安 K 线字段说明（只用我们需要的部分）：
            # item[0] 开盘时间(ms)
            # item[1] 开盘价
            # item[2] 最高价
            # item[3] 最低价
            # item[4] 收盘价
            # item[5] 成交量
            row_data = {
                "date": _ms_to_date(int(item[0])),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            }
            upsert_by_date(session=session, model=BtcPrice, row_data=row_data)
            affected += 1

        # 一批数据统一 commit，减少事务开销
        session.commit()

    return affected


def _fetch_range(start_date: str, end_date: str) -> int:
    """
    抓取指定日期区间的 BTC 日线并写入数据库。

    参数：
    - start_date: 起始日期（含）
    - end_date: 结束日期（含）

    返回：
    - 总处理条数（upsert 处理过的行数）
    """
    start_ms = _to_ms(start_date)
    # 币安 endTime 是“<=endTime”的过滤，给到当日 23:59:59
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1) - timedelta(milliseconds=1)
    end_ms = int(end_dt.timestamp() * 1000)

    total_rows = 0
    cursor_ms = start_ms

    while cursor_ms <= end_ms:
        klines = _request_klines_with_retry(start_time_ms=cursor_ms, end_time_ms=end_ms)
        if not klines:
            print("[完成] 币安返回空数据，抓取结束。")
            break

        # 保存本页数据
        batch_rows = _save_klines_to_db(klines)
        total_rows += batch_rows

        first_date = _ms_to_date(int(klines[0][0]))
        last_open_time = int(klines[-1][0])
        last_date = _ms_to_date(last_open_time)
        print(f"[进度] 本页 {batch_rows} 条，范围 {first_date} -> {last_date}，累计 {total_rows} 条")

        # 翻页：从最后一条 K 线的下一天开始
        next_ms = last_open_time + 24 * 60 * 60 * 1000
        if next_ms <= cursor_ms:
            # 理论上不会发生，作为安全保护防止死循环
            break
        cursor_ms = next_ms

        # 如果本页不足 1000，通常已经到末尾了
        if len(klines) < LIMIT:
            break

    return total_rows


def find_internal_btc_price_gap_ranges(sorted_dates: list[str]) -> list[tuple[str, str]]:
    """
    在「数据库里已有的最早日期」和「最晚日期」之间，找出日历上不连续的缺失区间。

    大白话：
    - 只检查“中间有没有漏天”，不检查尾部是否跟上今天（尾部由增量抓取负责）
    - 例如库里有 2024-01-01 和 2024-01-05，但没有 02~04，会返回 [("2024-01-02", "2024-01-04")]

    参数：
    - sorted_dates：已按字符串升序排好的 YYYY-MM-DD 列表（与 btc_price.date 一致）

    返回：
    - 若干 (起始日, 结束日) 闭区间；若无中间断层则返回空列表
    """
    if len(sorted_dates) < 2:
        return []

    have = set(sorted_dates)
    d_min = datetime.strptime(sorted_dates[0], "%Y-%m-%d").date()
    d_max = datetime.strptime(sorted_dates[-1], "%Y-%m-%d").date()

    ranges: list[tuple[str, str]] = []
    gap_start: date | None = None

    d = d_min
    one_day = timedelta(days=1)
    while d <= d_max:
        ds = d.strftime("%Y-%m-%d")
        if ds not in have:
            if gap_start is None:
                gap_start = d
        else:
            if gap_start is not None:
                # 遇到第一个“有数据”的日子，说明缺失段在昨天结束
                end_gap = d - one_day
                ranges.append((gap_start.strftime("%Y-%m-%d"), end_gap.strftime("%Y-%m-%d")))
                gap_start = None
        d = d + one_day

    return ranges


def fill_internal_price_gaps() -> dict[str, Any]:
    """
    扫描 btc_price：在已有 min(date)~max(date) 之间发现缺失日历日，则按区间向币安补抓并 upsert。

    返回字段（方便调度脚本判断要不要触发 Ahr999 等全量重算）：
    - gap_ranges: 本次识别到的缺失区间列表
    - rows_fetched: 补数阶段从 API 写入的总行数（upsert 计数）
    """
    init_db()
    with SessionLocal() as session:
        sorted_dates = list(session.execute(select(BtcPrice.date).order_by(BtcPrice.date.asc())).scalars().all())

    ranges = find_internal_btc_price_gap_ranges(sorted_dates)
    if not ranges:
        print("[检测] btc_price 在已有首尾日期之间未发现缺失日（中间无断层）。")
        return {"gap_ranges": [], "rows_fetched": 0}

    total_rows = 0
    for start, end in ranges:
        print(f"[补数] btc_price 发现中间断层，正在从币安补抓：{start} -> {end}（含首尾）")
        total_rows += _fetch_range(start_date=start, end_date=end)

    print(f"[补数] 中间断层补抓完成，本阶段累计写入/更新约 {total_rows} 条。")
    return {"gap_ranges": ranges, "rows_fetched": total_rows}


def fetch_full_history(start_date: str = FULL_START_DATE) -> int:
    """
    全量抓取（默认从 2018-01-01 到今天）。
    """
    init_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"[开始] 全量抓取 BTC 日线：{start_date} -> {today}")
    return _fetch_range(start_date=start_date, end_date=today)


def fetch_incremental() -> dict[str, Any]:
    """
    增量抓取（推荐由每日调度调用）：

    1) 先修补「已有最早~最晚日期」之间的中间断层（避免只追尾部导致历史永远有个洞）
    2) 再读取 btc_price 表最新日期，从「下一天」抓到「今天」（UTC 日历日）

    返回值（dict，便于 scheduler 判断是否需要触发 Ahr999 全量重算等）：
    - had_internal_gaps_filled: 是否在本次修补了中间断层
    - internal_gap_ranges: 识别到的缺失区间列表，元素为 (起, 止) 字符串日期
    - gap_fill_rows: 补断层阶段写入/更新的行数
    - tail_increment_rows: 追尾部新 K 线阶段写入/更新的行数
    - mode: \"gap_then_tail\" | \"full_history\"（无数据时走全量）
    """
    init_db()

    gap_report = fill_internal_price_gaps()
    gap_ranges = gap_report["gap_ranges"]
    gap_fill_rows = int(gap_report["rows_fetched"])

    with SessionLocal() as session:
        latest_date = session.scalar(select(func.max(BtcPrice.date)))

    if latest_date is None:
        print("[提示] 数据库暂无 BTC 价格数据，自动转为全量抓取。")
        full_rows = fetch_full_history()
        return {
            "had_internal_gaps_filled": gap_fill_rows > 0,
            "internal_gap_ranges": gap_ranges,
            "gap_fill_rows": gap_fill_rows,
            "tail_increment_rows": int(full_rows),
            "mode": "full_history",
        }

    next_date_dt = datetime.strptime(latest_date, "%Y-%m-%d") + timedelta(days=1)
    start_date = next_date_dt.strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    tail_rows = 0
    if start_date > today:
        print(f"[完成] 尾部已是最新，数据库最新日期：{latest_date}")
    else:
        print(f"[开始] 增量抓取 BTC 日线尾部：{start_date} -> {today}（数据库最新：{latest_date}）")
        tail_rows = _fetch_range(start_date=start_date, end_date=today)

    return {
        "had_internal_gaps_filled": gap_fill_rows > 0,
        "internal_gap_ranges": gap_ranges,
        "gap_fill_rows": gap_fill_rows,
        "tail_increment_rows": tail_rows,
        "mode": "gap_then_tail",
    }


def summarize_btc_price() -> dict[str, Any]:
    """
    读取 btc_price 表统计信息，方便抓取后验收。
    """
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(BtcPrice)) or 0
        min_date, max_date = session.execute(select(func.min(BtcPrice.date), func.max(BtcPrice.date))).one()

    return {
        "total_rows": int(total),
        "earliest_date": min_date,
        "latest_date": max_date,
    }


if __name__ == "__main__":
    # 直接运行本文件时，默认执行一次全量抓取并打印汇总。
    rows = fetch_full_history()
    summary = summarize_btc_price()
    print("\n[结果] 全量抓取执行完成")
    print(f"- 本次处理条数：{rows}")
    print(f"- 数据库总条数：{summary['total_rows']}")
    print(f"- 最早日期：{summary['earliest_date']}")
    print(f"- 最新日期：{summary['latest_date']}")
