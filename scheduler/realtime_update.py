"""
每 5 分钟实时更新指标当前值到 realtime_values 表，并检测实时告警。

执行：
    python -m scheduler.realtime_update

Crontab（deploy.sh）：
    */5 * * * * cd /opt/INDEX && /opt/INDEX/.venv/bin/python -m scheduler.realtime_update >> /opt/INDEX/logs/realtime_update.log 2>&1
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select

from app.db.database import SessionLocal, init_db, upsert_by_unique_keys
from app.db.models import Btc200wMa, Btc4yMa, BtcAhr999, BtcFearGreed, BtcPrice, BtcRsi, RealtimeValue
from btc.indicators_rsi import compute_wilder_rsi, load_price_history
from btc.percentile_runtime import calculate_dynamic_percentile
from btc.realtime import _fetch_realtime_price
from scheduler.alert_engine import check_realtime_alerts

DEFAULT_WINDOW = "2Y"
WINDOW_DAYS = 730


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
    window = s.iloc[-WINDOW_DAYS:]
    if window.empty:
        return None
    w = pd.concat([window, pd.Series([current], dtype="float64")], ignore_index=True)
    return calculate_dynamic_percentile(w, float(current))


def run_realtime_update(window: str = DEFAULT_WINDOW) -> dict[str, float | None]:
    init_db()
    price_now = _fetch_realtime_price()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with SessionLocal() as session:
        # RSI 统一用 Binance 日 K 线口径，避免与交易所显示不一致
        close_df = load_price_history()
        close_series = pd.to_numeric(close_df["close"], errors="coerce").dropna().astype("float64")
        rsi6_now = _to_float(compute_wilder_rsi(close_series, period=6).iloc[-1]) if not close_series.empty else None
        rsi12_now = _to_float(compute_wilder_rsi(close_series, period=12).iloc[-1]) if not close_series.empty else None

        latest_4y = session.execute(select(Btc4yMa).order_by(Btc4yMa.date.desc()).limit(1)).scalar_one_or_none()
        latest_200w = session.execute(select(Btc200wMa).order_by(Btc200wMa.date.desc()).limit(1)).scalar_one_or_none()
        latest_ahr = session.execute(select(BtcAhr999).order_by(BtcAhr999.date.desc()).limit(1)).scalar_one_or_none()
        latest_price = session.execute(select(BtcPrice).order_by(BtcPrice.date.desc()).limit(1)).scalar_one_or_none()
        latest_fg = session.execute(select(BtcFearGreed).order_by(BtcFearGreed.date.desc()).limit(1)).scalar_one_or_none()

        ma4y = _to_float(getattr(latest_4y, "ma_value", None))
        ma200w = _to_float(getattr(latest_200w, "ma_value", None))
        ratio_4y = (float(price_now) / ma4y) if ma4y else None
        ratio_200w = (float(price_now) / ma200w) if ma200w else None

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
        values: dict[str, float | None] = {
            "close": float(price_now),
            "rsi6": rsi6_now,
            "rsi12": rsi12_now,
            "rsi6_pct_ui": _compute_pct_2y(rsi6_hist, rsi6_now),
            "rsi12_pct_ui": _compute_pct_2y(rsi12_hist, rsi12_now),
            "value": fg_now,
            "fg_pct_ui": _compute_pct_2y(fg_hist, fg_now),
            "ahr999_value": ahr_now,
            "ahr999_pct_ui": _compute_pct_2y(ahr_hist, ahr_now),
            "ma_value(4y)": ma4y,
            "price_to_4y_ma": ratio_4y,
            "ma_value(200w)": ma200w,
            "price_to_200w_ma": ratio_200w,
        }

        for indicator_code, current_value in values.items():
            upsert_by_unique_keys(
                session=session,
                model=RealtimeValue,
                row_data={
                    "indicator_code": indicator_code,
                    "current_value": current_value,
                    "update_time": now_str,
                    "window": window,
                },
                unique_keys=["indicator_code", "window"],
            )
        session.commit()

    return values


if __name__ == "__main__":
    payload = run_realtime_update()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[realtime_update] updated {len(payload)} indicators @ {ts}")

    alert_result = check_realtime_alerts()
    print(
        f"[realtime_update] alerts score={alert_result.total_score} "
        f"triggered={alert_result.triggered_rules} sent={alert_result.sent_rules} "
        f"skipped={alert_result.skipped_rules}"
    )
