"""
为 btc_alert_dedup 增加 total_score、triggered_rules 列（SQLite 幂等）。

用法：
    python3 scripts/migrate_extend_btc_alert_dedup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import inspect, text

from app.db.database import engine, init_db


def _has_column(table: str, column: str) -> bool:
    insp = inspect(engine)
    return any(c["name"] == column for c in insp.get_columns(table))


def main() -> None:
    init_db()
    with engine.begin() as conn:
        if not _has_column("btc_alert_dedup", "total_score"):
            conn.execute(text("ALTER TABLE btc_alert_dedup ADD COLUMN total_score INTEGER"))
            print("[migrate] +total_score")
        if not _has_column("btc_alert_dedup", "triggered_rules"):
            conn.execute(text("ALTER TABLE btc_alert_dedup ADD COLUMN triggered_rules VARCHAR(128)"))
            print("[migrate] +triggered_rules")
    print("[migrate] btc_alert_dedup 扩展列已就绪。")


if __name__ == "__main__":
    main()
