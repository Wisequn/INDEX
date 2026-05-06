"""
SQLite 轻量 schema 迁移（非 Alembic）。

说明：
- SQLAlchemy 的 create_all() 不会给「已存在的表」自动删列
- 本模块在 init_db() 末尾调用，幂等清理「已废弃的预计算百分位」列与 btc_rsi_percentile 表
"""

from __future__ import annotations

from sqlalchemy import Engine, text

# 旧版入库的百分位列：现已改为前端内存动态计算，启动时尽量 DROP 掉（SQLite 3.35+）
_LEGACY_DROP_COLUMNS: dict[str, tuple[str, ...]] = {
    "btc_fear_greed": ("fg_pct_1y", "fg_pct_2y", "fg_pct_3y", "fg_pct_4y", "fg_pct_all"),
    "btc_ahr999": ("ahr999_pct_1y", "ahr999_pct_2y", "ahr999_pct_3y", "ahr999_pct_4y", "ahr999_pct_all"),
    "btc_4y_ma": ("p4yma_pct_1y", "p4yma_pct_4y", "p4yma_pct_all"),
    "btc_200w_ma": ("p200wma_pct_1y", "p200wma_pct_4y", "p200wma_pct_all"),
}


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {str(r[1]) for r in rows}


def apply_legacy_percentile_cleanup(engine: Engine) -> list[str]:
    """
    删除 btc_rsi_percentile 表及各 btc_* 表上的 *_pct_* 列（若存在则执行）。

    返回：
    - 本次实际执行过的 DDL 说明列表
    """
    applied: list[str] = []

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='btc_rsi_percentile'")
        ).fetchone()
        if exists:
            conn.execute(text("DROP TABLE btc_rsi_percentile"))
            applied.append("DROP TABLE btc_rsi_percentile")

        for table, cols in _LEGACY_DROP_COLUMNS.items():
            try:
                existing = _table_columns(conn, table)
            except Exception:
                continue
            for col in cols:
                if col not in existing:
                    continue
                ddl = f'ALTER TABLE "{table}" DROP COLUMN "{col}"'
                try:
                    conn.execute(text(ddl))
                    applied.append(ddl)
                except Exception as exc:
                    applied.append(f"-- skip {ddl}: {exc}")

    return applied
