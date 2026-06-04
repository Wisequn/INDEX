"""
建表迁移：alert_history（实时告警防重复：总分变化或满 4 小时可再发）。

用法（在仓库根目录）：
    python3 scripts/migrate_add_alert_history.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import inspect, text

from app.db.database import engine, init_db


def _has_table(name: str) -> bool:
    return name in inspect(engine).get_table_names()


def _migrate_from_btc_alert_dedup() -> None:
    """将旧表 btc_alert_dedup 中每条规则最近一次发送迁入 alert_history。"""
    if not _has_table("btc_alert_dedup"):
        return
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT rule_code, sent_at, total_score
                FROM btc_alert_dedup
                WHERE sent_at IS NOT NULL
                ORDER BY rule_code, sent_at DESC
                """
            )
        ).fetchall()
        seen: set[str] = set()
        for rule_code, sent_at, total_score in rows:
            if rule_code in seen:
                continue
            seen.add(str(rule_code))
            score = int(total_score) if total_score is not None else 0
            conn.execute(
                text(
                    """
                    INSERT OR IGNORE INTO alert_history (rule_code, last_sent_time, last_total_score)
                    VALUES (:rule_code, :last_sent_time, :last_total_score)
                    """
                ),
                {
                    "rule_code": rule_code,
                    "last_sent_time": sent_at,
                    "last_total_score": score,
                },
            )
    print("[migrate] 已从 btc_alert_dedup 导入历史（如有）。")


def main() -> None:
    init_db()
    _migrate_from_btc_alert_dedup()
    print("[migrate] alert_history 表已就绪（create_all 幂等）。")


if __name__ == "__main__":
    main()
