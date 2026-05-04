"""
SQLite 轻量 schema 迁移（非 Alembic）。

说明：
- SQLAlchemy 的 create_all() 不会给「已存在的表」自动加新列
- 本模块在 init_db() 末尾调用，用 PRAGMA + ALTER TABLE 幂等补列
"""

from __future__ import annotations

from sqlalchemy import Engine, text


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
    return {str(r[1]) for r in rows}


def apply_multiyear_percentile_migrations(engine: Engine) -> list[str]:
    """
    为 RSI / 恐惧贪婪 / Ahr999 百分位表补齐 2Y、3Y 等列（若已存在则跳过）。

    返回：
    - 本次实际执行过的 ALTER 语句说明列表（便于日志打印）
    """
    alterations: list[tuple[str, str, str]] = [
        ("btc_rsi_percentile", "rsi6_pct_2y", "FLOAT"),
        ("btc_rsi_percentile", "rsi6_pct_3y", "FLOAT"),
        ("btc_rsi_percentile", "rsi12_pct_2y", "FLOAT"),
        ("btc_rsi_percentile", "rsi12_pct_3y", "FLOAT"),
        ("btc_fear_greed", "fg_pct_2y", "FLOAT"),
        ("btc_fear_greed", "fg_pct_3y", "FLOAT"),
        ("btc_ahr999", "ahr999_pct_2y", "FLOAT"),
        ("btc_ahr999", "ahr999_pct_3y", "FLOAT"),
    ]

    applied: list[str] = []

    with engine.begin() as conn:
        for table, col, sql_type in alterations:
            try:
                existing = _table_columns(conn, table)
            except Exception:
                # 表尚未创建时跳过（首次空库 create_all 之后会再有数据）
                continue
            if col in existing:
                continue
            ddl = f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}"
            conn.execute(text(ddl))
            applied.append(ddl)

    return applied
