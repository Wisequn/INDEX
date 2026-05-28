"""
BTC 抄底分数（Bottom Score）打分引擎 + 实时告警 + 每日早报集成。

模块结构
--------
1. 数据类型      BottomScoreInputs, RealtimeAlertResult
2. 打分核心      calculate_total_score()
3. 输入构建      build_inputs_for_date(), build_inputs_realtime()
4. 分数持久化    persist_bottom_score_for_date()
5. 实时告警      check_realtime_alerts()  ← realtime_update 每 5 分钟调用
6. 每日早报      run_daily_morning_briefing() → daily_morning_report
7. CLI           python -m scheduler.alert_engine --test / --alerts / ...

集成关系
--------
- 实时：build_inputs_realtime → calculate_total_score + evaluate_triggered_alert_rules → 推送
- 早报：daily_morning_report → build_inputs_for_date → calculate_total_score → btc_bottom_score → 推送
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.db.database import SessionLocal, init_db, upsert_by_date, upsert_by_unique_keys
from app.db.models import (
    Btc4yMa,
    BtcAhr999,
    BtcAlertDedup,
    BtcBottomScore,
    BtcFearGreed,
    BtcPrice,
    BtcRsi,
)
from app.services.alert_notifier import send_alert_message
from btc.indicators_rsi import compute_wilder_rsi, load_price_history
from btc.percentile_runtime import calculate_dynamic_percentile
from btc.realtime import _fetch_realtime_price

WINDOW_DAYS_2Y = 730


@dataclass(frozen=True)
class BottomScoreInputs:
    """单日打分所需的全部原始值与 2Y 百分位。"""

    close: float | None = None
    close_2y_low: float | None = None
    rsi6: float | None = None
    rsi6_pct_ui: float | None = None
    rsi12: float | None = None
    rsi12_pct_ui: float | None = None
    value: float | None = None
    fg_pct_ui: float | None = None
    ahr999_value: float | None = None
    ahr999_pct_ui: float | None = None
    price_to_4y_ma: float | None = None


@dataclass(frozen=True)
class RealtimeAlertResult:
    """单次实时告警检测结果。"""

    total_score: int
    triggered_rules: list[str]
    sent_rules: list[str]
    skipped_rules: list[str]


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_REALTIME_ALERT_LOG = _PROJECT_ROOT / "logs" / "realtime_alerts.log"


def _ab_pair_score(a_hit: bool, a_points: int, b_hit: bool, b_points: int) -> int:
    """B 触发时返回 B 分；否则 A 触发返回 A 分；否则 0。"""
    if b_hit:
        return b_points
    if a_hit:
        return a_points
    return 0


def _merge_score_inputs(
    *,
    close: float | None = None,
    close_2y_low: float | None = None,
    rsi6: float | None = None,
    rsi6_pct_ui: float | None = None,
    rsi12: float | None = None,
    rsi12_pct_ui: float | None = None,
    value: float | None = None,
    fg_pct_ui: float | None = None,
    ahr999_value: float | None = None,
    ahr999_pct_ui: float | None = None,
    price_to_4y_ma: float | None = None,
    inputs: BottomScoreInputs | None = None,
) -> BottomScoreInputs:
    if inputs is not None:
        close = close if close is not None else inputs.close
        close_2y_low = close_2y_low if close_2y_low is not None else inputs.close_2y_low
        rsi6 = rsi6 if rsi6 is not None else inputs.rsi6
        rsi6_pct_ui = rsi6_pct_ui if rsi6_pct_ui is not None else inputs.rsi6_pct_ui
        rsi12 = rsi12 if rsi12 is not None else inputs.rsi12
        rsi12_pct_ui = rsi12_pct_ui if rsi12_pct_ui is not None else inputs.rsi12_pct_ui
        value = value if value is not None else inputs.value
        fg_pct_ui = fg_pct_ui if fg_pct_ui is not None else inputs.fg_pct_ui
        ahr999_value = ahr999_value if ahr999_value is not None else inputs.ahr999_value
        ahr999_pct_ui = ahr999_pct_ui if ahr999_pct_ui is not None else inputs.ahr999_pct_ui
        price_to_4y_ma = price_to_4y_ma if price_to_4y_ma is not None else inputs.price_to_4y_ma
    return BottomScoreInputs(
        close=close,
        close_2y_low=close_2y_low,
        rsi6=rsi6,
        rsi6_pct_ui=rsi6_pct_ui,
        rsi12=rsi12,
        rsi12_pct_ui=rsi12_pct_ui,
        value=value,
        fg_pct_ui=fg_pct_ui,
        ahr999_value=ahr999_value,
        ahr999_pct_ui=ahr999_pct_ui,
        price_to_4y_ma=price_to_4y_ma,
    )


def get_factor_scores(
    *,
    close: float | None = None,
    close_2y_low: float | None = None,
    rsi6: float | None = None,
    rsi6_pct_ui: float | None = None,
    rsi12: float | None = None,
    rsi12_pct_ui: float | None = None,
    value: float | None = None,
    fg_pct_ui: float | None = None,
    ahr999_value: float | None = None,
    ahr999_pct_ui: float | None = None,
    price_to_4y_ma: float | None = None,
    inputs: BottomScoreInputs | None = None,
) -> dict[str, int]:
    """
    返回 13 个因子的单项得分（未触发为 0）。
    A/B 对中 B 触发时 A 为 0；5C 可与 5A/5B 同时非 0。
    """
    inp = _merge_score_inputs(
        close=close,
        close_2y_low=close_2y_low,
        rsi6=rsi6,
        rsi6_pct_ui=rsi6_pct_ui,
        rsi12=rsi12,
        rsi12_pct_ui=rsi12_pct_ui,
        value=value,
        fg_pct_ui=fg_pct_ui,
        ahr999_value=ahr999_value,
        ahr999_pct_ui=ahr999_pct_ui,
        price_to_4y_ma=price_to_4y_ma,
        inputs=inputs,
    )

    hit_1a = (
        inp.close is not None
        and inp.close_2y_low is not None
        and inp.close == inp.close_2y_low
    )
    hit_2a = (
        inp.rsi6 is not None
        and inp.rsi6_pct_ui is not None
        and inp.rsi6 < 12
        and inp.rsi6_pct_ui < 5
    )
    hit_2b = (
        inp.rsi6 is not None
        and inp.rsi6_pct_ui is not None
        and inp.rsi6 < 12
        and inp.rsi6_pct_ui < 1
    )
    hit_3a = (
        inp.rsi12 is not None
        and inp.rsi12_pct_ui is not None
        and inp.rsi12 < 25
        and inp.rsi12_pct_ui < 5
    )
    hit_3b = (
        inp.rsi12 is not None
        and inp.rsi12_pct_ui is not None
        and inp.rsi12 < 15
        and inp.rsi12_pct_ui < 1
    )
    hit_4a = (
        inp.value is not None
        and inp.fg_pct_ui is not None
        and inp.value < 12
        and inp.fg_pct_ui < 2
    )
    hit_4b = (
        inp.value is not None
        and inp.fg_pct_ui is not None
        and inp.value <= 8
        and inp.fg_pct_ui < 0.5
    )
    hit_5a = (
        inp.ahr999_value is not None
        and inp.ahr999_pct_ui is not None
        and inp.ahr999_value < 0.85
        and inp.ahr999_pct_ui < 3
    )
    hit_5b = (
        inp.ahr999_value is not None
        and inp.ahr999_pct_ui is not None
        and inp.ahr999_value < 0.45
        and inp.ahr999_pct_ui < 3
    )
    hit_5c = inp.ahr999_value is not None and inp.ahr999_value < 0.35
    hit_6a = inp.price_to_4y_ma is not None and inp.price_to_4y_ma < 1.1
    hit_6b = inp.price_to_4y_ma is not None and inp.price_to_4y_ma < 0.8

    return {
        "1A": -5 if hit_1a else 0,
        "2A": -5 if hit_2a and not hit_2b else 0,
        "2B": -10 if hit_2b else 0,
        "3A": -5 if hit_3a and not hit_3b else 0,
        "3B": -10 if hit_3b else 0,
        "4A": -5 if hit_4a and not hit_4b else 0,
        "4B": -10 if hit_4b else 0,
        "5A": -5 if hit_5a and not hit_5b else 0,
        "5B": -10 if hit_5b else 0,
        "5C": -15 if hit_5c else 0,
        "6A": -5 if hit_6a and not hit_6b else 0,
        "6B": -10 if hit_6b else 0,
    }


def calculate_total_score(
    *,
    close: float | None = None,
    close_2y_low: float | None = None,
    rsi6: float | None = None,
    rsi6_pct_ui: float | None = None,
    rsi12: float | None = None,
    rsi12_pct_ui: float | None = None,
    value: float | None = None,
    fg_pct_ui: float | None = None,
    ahr999_value: float | None = None,
    ahr999_pct_ui: float | None = None,
    price_to_4y_ma: float | None = None,
    inputs: BottomScoreInputs | None = None,
) -> int:
    """
    按 B 规则（13 因子）计算抄底分数总分，返回 int。

    也可传入 BottomScoreInputs 实例（关键字参数优先于 inputs 字段）。
    """
    scores = get_factor_scores(
        close=close,
        close_2y_low=close_2y_low,
        rsi6=rsi6,
        rsi6_pct_ui=rsi6_pct_ui,
        rsi12=rsi12,
        rsi12_pct_ui=rsi12_pct_ui,
        value=value,
        fg_pct_ui=fg_pct_ui,
        ahr999_value=ahr999_value,
        ahr999_pct_ui=ahr999_pct_ui,
        price_to_4y_ma=price_to_4y_ma,
        inputs=inputs,
    )
    return int(sum(scores.values()))


def _pct_2y_at_date(series_by_date: pd.Series, target_date: str) -> float | None:
    """截至 target_date（含）最近 730 日内，对当日值做 rank 百分位。"""
    s = pd.to_numeric(series_by_date, errors="coerce")
    if target_date not in s.index:
        return None
    cur = s.loc[target_date]
    if pd.isna(cur):
        return None
    hist = s.loc[:target_date].dropna()
    if hist.empty:
        return None
    window = hist.iloc[-WINDOW_DAYS_2Y:]
    if window.empty:
        return None
    w = pd.concat([window, pd.Series([float(cur)], dtype="float64")], ignore_index=True)
    p = calculate_dynamic_percentile(w, float(cur))
    return None if p is None else float(p)


def build_inputs_for_date(session: Any, date: str) -> BottomScoreInputs | None:
    """从库中组装指定交易日的打分输入；缺价格行则返回 None。"""
    price_row = session.execute(select(BtcPrice).where(BtcPrice.date == date)).scalar_one_or_none()
    if price_row is None:
        return None

    close = float(price_row.close)
    price_hist = session.execute(select(BtcPrice.date, BtcPrice.close).order_by(BtcPrice.date.asc())).all()
    price_df = pd.DataFrame(price_hist, columns=["date", "close"]).set_index("date")
    price_df["close"] = pd.to_numeric(price_df["close"], errors="coerce")

    hist_to_date = price_df.loc[:date]
    window = hist_to_date.iloc[-WINDOW_DAYS_2Y:]
    close_2y_low = float(window["close"].min()) if not window.empty else None

    rsi_row = session.execute(select(BtcRsi).where(BtcRsi.date == date)).scalar_one_or_none()
    fg_row = session.execute(select(BtcFearGreed).where(BtcFearGreed.date == date)).scalar_one_or_none()
    ahr_row = session.execute(select(BtcAhr999).where(BtcAhr999.date == date)).scalar_one_or_none()
    ma4_row = session.execute(select(Btc4yMa).where(Btc4yMa.date == date)).scalar_one_or_none()

    rsi_hist = session.execute(select(BtcRsi.date, BtcRsi.rsi6, BtcRsi.rsi12).order_by(BtcRsi.date.asc())).all()
    rsi_df = pd.DataFrame(rsi_hist, columns=["date", "rsi6", "rsi12"]).set_index("date")
    fg_hist = session.execute(select(BtcFearGreed.date, BtcFearGreed.value).order_by(BtcFearGreed.date.asc())).all()
    fg_df = pd.DataFrame(fg_hist, columns=["date", "value"]).set_index("date")
    ahr_hist = session.execute(
        select(BtcAhr999.date, BtcAhr999.ahr999_value).order_by(BtcAhr999.date.asc())
    ).all()
    ahr_df = pd.DataFrame(ahr_hist, columns=["date", "ahr999_value"]).set_index("date")

    rsi6 = float(rsi_row.rsi6) if rsi_row is not None and rsi_row.rsi6 is not None else None
    rsi12 = float(rsi_row.rsi12) if rsi_row is not None and rsi_row.rsi12 is not None else None
    value = float(fg_row.value) if fg_row is not None and fg_row.value is not None else None
    ahr999_value = (
        float(ahr_row.ahr999_value) if ahr_row is not None and ahr_row.ahr999_value is not None else None
    )
    price_to_4y_ma = (
        float(ma4_row.price_to_4y_ma)
        if ma4_row is not None and ma4_row.price_to_4y_ma is not None
        else None
    )

    return BottomScoreInputs(
        close=close,
        close_2y_low=close_2y_low,
        rsi6=rsi6,
        rsi6_pct_ui=_pct_2y_at_date(rsi_df["rsi6"], date),
        rsi12=rsi12,
        rsi12_pct_ui=_pct_2y_at_date(rsi_df["rsi12"], date),
        value=value,
        fg_pct_ui=_pct_2y_at_date(fg_df["value"], date),
        ahr999_value=ahr999_value,
        ahr999_pct_ui=_pct_2y_at_date(ahr_df["ahr999_value"], date),
        price_to_4y_ma=price_to_4y_ma,
    )


def persist_bottom_score_for_date(date: str, score: int | None = None) -> int:
    """
    计算并 upsert 指定日期的抄底分数；score 可显式传入（用于测试）。
    返回写入的 score。
    """
    init_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with SessionLocal() as session:
        if score is None:
            inputs = build_inputs_for_date(session, date)
            if inputs is None:
                raise ValueError(f"无法为 {date} 构建打分输入（缺少 btc_price 行）")
            score = calculate_total_score(inputs=inputs)

        upsert_by_date(
            session,
            BtcBottomScore,
            {"date": date, "score": int(score), "created_at": now_str},
        )
        session.commit()

    return int(score)


def _to_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compute_pct_2y(series: pd.Series, current: float | None) -> float | None:
    if current is None:
        return None
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    window = s.iloc[-WINDOW_DAYS_2Y:]
    if window.empty:
        return None
    w = pd.concat([window, pd.Series([float(current)], dtype="float64")], ignore_index=True)
    p = calculate_dynamic_percentile(w, float(current))
    return None if p is None else float(p)


def build_inputs_realtime() -> BottomScoreInputs:
    """用 Binance 现价 + 库内历史，组装实时告警/打分输入。"""
    init_db()
    price_now = _fetch_realtime_price()

    with SessionLocal() as session:
        close_df = load_price_history()
        close_series = pd.to_numeric(close_df["close"], errors="coerce").dropna().astype("float64")

        window = close_series.iloc[-WINDOW_DAYS_2Y:] if len(close_series) else close_series
        if len(window):
            close_2y_low = float(min(window.min(), float(price_now)))
        else:
            close_2y_low = float(price_now)

        rsi6_now = _to_float(compute_wilder_rsi(close_series, period=6).iloc[-1]) if not close_series.empty else None
        rsi12_now = _to_float(compute_wilder_rsi(close_series, period=12).iloc[-1]) if not close_series.empty else None

        latest_4y = session.execute(select(Btc4yMa).order_by(Btc4yMa.date.desc()).limit(1)).scalar_one_or_none()
        latest_ahr = session.execute(select(BtcAhr999).order_by(BtcAhr999.date.desc()).limit(1)).scalar_one_or_none()
        latest_price = session.execute(select(BtcPrice).order_by(BtcPrice.date.desc()).limit(1)).scalar_one_or_none()
        latest_fg = session.execute(select(BtcFearGreed).order_by(BtcFearGreed.date.desc()).limit(1)).scalar_one_or_none()

        ma4y = _to_float(getattr(latest_4y, "ma_value", None))
        ratio_4y = (float(price_now) / ma4y) if ma4y else None

        ahr_now = None
        if latest_ahr is not None and latest_price is not None and _to_float(latest_price.close):
            ahr_now = float(latest_ahr.ahr999_value) * float(price_now) / float(latest_price.close)

        rsi_rows = session.execute(select(BtcRsi.rsi6, BtcRsi.rsi12).order_by(BtcRsi.date.asc())).all()
        rsi6_hist = pd.Series([r.rsi6 for r in rsi_rows], dtype="float64")
        rsi12_hist = pd.Series([r.rsi12 for r in rsi_rows], dtype="float64")
        fg_hist = pd.Series(
            [r.value for r in session.execute(select(BtcFearGreed.value).order_by(BtcFearGreed.date.asc())).all()],
            dtype="float64",
        )
        ahr_hist = pd.Series(
            [r.ahr999_value for r in session.execute(select(BtcAhr999.ahr999_value).order_by(BtcAhr999.date.asc())).all()],
            dtype="float64",
        )
        fg_now = _to_float(getattr(latest_fg, "value", None))

    return BottomScoreInputs(
        close=float(price_now),
        close_2y_low=close_2y_low,
        rsi6=rsi6_now,
        rsi6_pct_ui=_compute_pct_2y(rsi6_hist, rsi6_now),
        rsi12=rsi12_now,
        rsi12_pct_ui=_compute_pct_2y(rsi12_hist, rsi12_now),
        value=fg_now,
        fg_pct_ui=_compute_pct_2y(fg_hist, fg_now),
        ahr999_value=ahr_now,
        ahr999_pct_ui=_compute_pct_2y(ahr_hist, ahr_now),
        price_to_4y_ma=ratio_4y,
    )


def _alert_already_sent(session: Any, rule_code: str, bucket_date: str) -> bool:
    row = session.execute(
        select(BtcAlertDedup).where(
            BtcAlertDedup.rule_code == rule_code,
            BtcAlertDedup.bucket_date == bucket_date,
        )
    ).scalar_one_or_none()
    return row is not None


def _alerts_push_enabled() -> bool:
    raw = os.getenv("ALERT_PUSH_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _log_realtime_alert_check(result: RealtimeAlertResult) -> None:
    """写入 logs/realtime_alerts.log；无权限时仅打印，不中断主流程。"""
    payload = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_score": result.total_score,
        "triggered_rules": result.triggered_rules,
        "sent_rules": result.sent_rules,
        "skipped_rules": result.skipped_rules,
    }
    line = json.dumps(payload, ensure_ascii=False)
    try:
        _REALTIME_ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_REALTIME_ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        print(f"[alert_engine] 无法写入 {_REALTIME_ALERT_LOG}: {exc}")
        print(f"[alert_engine] {line}")


def check_realtime_alerts(*, dry_run: bool = False, push: bool | None = None) -> RealtimeAlertResult:
    """
    实时告警主入口（由 scheduler.realtime_update 每 5 分钟调用）。

    1. build_inputs_realtime() 组装输入
    2. calculate_total_score() 计算当前抄底分数
    3. evaluate_triggered_alert_rules() 判定触发因子
    4. 按规则推送（日内去重），并记录 total_score / triggered_rules
    """
    from scheduler.alert_messages import evaluate_triggered_alert_rules, format_alert_message

    if push is None:
        push = _alerts_push_enabled()

    init_db()
    inp = build_inputs_realtime()
    total_score = calculate_total_score(inputs=inp)
    triggered_rules = evaluate_triggered_alert_rules(inp)
    bucket_date = datetime.now().strftime("%Y-%m-%d")
    sent_rules: list[str] = []
    skipped_rules: list[str] = []
    rules_csv = ",".join(triggered_rules)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with SessionLocal() as session:
        for rule in triggered_rules:
            if _alert_already_sent(session, rule, bucket_date):
                skipped_rules.append(rule)
                continue
            if not push:
                continue
            text = format_alert_message(rule, inp)
            send_alert_message(text, dry_run=dry_run)
            if not dry_run:
                upsert_by_unique_keys(
                    session=session,
                    model=BtcAlertDedup,
                    row_data={
                        "rule_code": rule,
                        "bucket_date": bucket_date,
                        "sent_at": now_str,
                        "total_score": total_score,
                        "triggered_rules": rules_csv,
                    },
                    unique_keys=["rule_code", "bucket_date"],
                )
            sent_rules.append(rule)
        if not dry_run and push and sent_rules:
            session.commit()

    result = RealtimeAlertResult(
        total_score=total_score,
        triggered_rules=triggered_rules,
        sent_rules=sent_rules,
        skipped_rules=skipped_rules,
    )
    _log_realtime_alert_check(result)
    return result


def run_realtime_alerts(*, dry_run: bool = False) -> list[str]:
    """兼容旧名：返回本次新发送的规则代码列表。"""
    return check_realtime_alerts(dry_run=dry_run).sent_rules


def run_daily_morning_briefing(*, dry_run: bool = False) -> str:
    """兼容入口：请优先使用 scheduler.daily_morning_report。"""
    from scheduler.daily_morning_report import run_daily_morning_report

    return run_daily_morning_report(dry_run=dry_run)


def _run_self_tests() -> None:
    """内置规则用例，可直接 python -m scheduler.alert_engine 验证。"""
    cases: list[tuple[BottomScoreInputs, int, str]] = [
        (BottomScoreInputs(), 0, "全空"),
        (
            BottomScoreInputs(close=100.0, close_2y_low=100.0),
            -5,
            "仅 1A",
        ),
        (
            BottomScoreInputs(rsi6=10.0, rsi6_pct_ui=0.5),
            -10,
            "2B 覆盖 2A",
        ),
        (
            BottomScoreInputs(rsi6=10.0, rsi6_pct_ui=3.0),
            -5,
            "仅 2A",
        ),
        (
            BottomScoreInputs(ahr999_value=0.30),
            -15,
            "仅 5C",
        ),
        (
            BottomScoreInputs(
                close=50.0,
                close_2y_low=50.0,
                rsi6=10.0,
                rsi6_pct_ui=0.5,
                ahr999_value=0.30,
                price_to_4y_ma=0.7,
            ),
            -5 - 10 - 15 - 10,
            "1A+2B+5C+6B",
        ),
    ]
    for inputs, expected, name in cases:
        got = calculate_total_score(inputs=inputs)
        assert got == expected, f"{name}: expected {expected}, got {got}"
        assert sum(get_factor_scores(inputs=inputs).values()) == expected, f"{name}: factor sum mismatch"
    print("✅ calculate_total_score / get_factor_scores 内置用例全部通过")


def _run_alert_message_tests() -> None:
    from scheduler.alert_messages import (
        ALERT_TEMPLATES,
        evaluate_triggered_alert_rules,
        format_alert_message,
        format_daily_briefing,
        BriefingSnapshot,
    )

    inp = BottomScoreInputs(rsi6=10.0, rsi6_pct_ui=0.5, close=90000.0)
    assert evaluate_triggered_alert_rules(inp) == ["2B"]
    msg = format_alert_message("2B", inp)
    assert "RSI6=10.00" in msg and "90000.00" in msg
    assert msg == ALERT_TEMPLATES["2B"].format(
        close="90000.00",
        rsi6="10.00",
        rsi6_pct_ui="0.50",
        rsi12="N/A",
        rsi12_pct_ui="N/A",
        value="N/A",
        fg_pct_ui="N/A",
        ahr999_value="N/A",
        ahr999_pct_ui="N/A",
        price_to_4y_ma="N/A",
    )

    brief = BriefingSnapshot(
        close=100.0,
        total_score=-25,
        report_date="2026-05-27",
        price_to_200w_ma=1.2,
    )
    text = format_daily_briefing(brief)
    assert "Index 每日早报 (2026-05-27 09:00)" in text
    assert "25 / 100" in text
    print("✅ 告警模板与触发规则用例全部通过")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BTC 抄底分数引擎")
    parser.add_argument("--test", action="store_true", help="仅跑内置打分用例")
    parser.add_argument("--test-alerts", action="store_true", help="跑告警模板与触发用例")
    parser.add_argument("--alerts", action="store_true", help="执行一次实时告警检测并推送")
    parser.add_argument("--alerts-dry-run", action="store_true", help="实时告警仅写本地日志")
    parser.add_argument("--daily-briefing", action="store_true", help="发送每日早报")
    parser.add_argument("--daily-briefing-dry-run", action="store_true", help="早报仅写本地日志")
    parser.add_argument("--date", type=str, help="计算并入库指定日期 YYYY-MM-DD")
    parser.add_argument("--latest", action="store_true", help="对库中最新价格日入库")
    args = parser.parse_args()

    if args.test:
        _run_self_tests()
    elif args.test_alerts:
        _run_alert_message_tests()
    elif args.alerts or args.alerts_dry_run:
        result = check_realtime_alerts(dry_run=args.alerts_dry_run, push=True)
        print(
            f"[alerts] score={result.total_score} "
            f"triggered={result.triggered_rules} sent={result.sent_rules} skipped={result.skipped_rules}"
        )
    elif args.daily_briefing or args.daily_briefing_dry_run:
        text = run_daily_morning_briefing(dry_run=args.daily_briefing_dry_run)
        print(text)
    elif args.date:
        s = persist_bottom_score_for_date(args.date)
        print(f"[bottom_score] {args.date} score={s}")
    elif args.latest:
        init_db()
        with SessionLocal() as session:
            latest = session.execute(select(BtcPrice.date).order_by(BtcPrice.date.desc()).limit(1)).scalar_one()
        s = persist_bottom_score_for_date(str(latest))
        print(f"[bottom_score] {latest} score={s}")
    else:
        _run_self_tests()
        print("提示: --date YYYY-MM-DD 或 --latest 写入 btc_bottom_score")
