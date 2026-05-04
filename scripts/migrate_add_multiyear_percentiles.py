#!/usr/bin/env python3
"""
一次性迁移：为 SQLite 补齐多窗口百分位列（2Y / 3Y 等）。

用法（在项目根目录）：
    python3 scripts/migrate_add_multiyear_percentiles.py

说明：
- 日常跑 `init_db()` 时也会自动执行相同逻辑；本脚本方便你在服务器上单独确认迁移是否执行成功。
"""

from __future__ import annotations

from app.db.database import engine, init_db
from app.db.schema_migrations import apply_multiyear_percentile_migrations


def main() -> None:
    init_db()
    applied = apply_multiyear_percentile_migrations(engine)
    if not applied:
        print("[migrate] 无需变更（列已齐全或表尚未创建）。")
    else:
        for ddl in applied:
            print(f"[migrate] OK: {ddl}")


if __name__ == "__main__":
    main()
