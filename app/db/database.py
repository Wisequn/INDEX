"""
数据库连接与建表模块（database.py）。

本文件负责 4 件事：
1) 连接 SQLite 数据库：index_monitor.db
2) 提供 SessionLocal（数据库会话工厂）
3) 提供 init_db()（创建所有表）
4) 提供通用 upsert 方法（有则更新、无则插入，避免重复数据）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# SQLite 数据库文件路径。所有表都存在这个文件里。
DATABASE_URL = "sqlite:///./index_monitor.db"

# 创建数据库引擎。
# future=True：使用 SQLAlchemy 2.x 推荐风格。
engine = create_engine(DATABASE_URL, echo=False, future=True)

# 会话工厂。后续每次读写数据库，都用 SessionLocal() 创建 session。
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# ORM 基类。models.py 里的表类都继承这个 Base。
Base = declarative_base()


def init_db() -> None:
    """
    初始化数据库并创建表。

    注意：
    - 这里只定义函数，不会自动执行
    - 你确认后，再在脚本里主动调用 init_db()
    """
    # 放在函数内部导入，避免循环导入问题：
    # models.py 依赖 Base，而这里需要加载 models 才能 create_all。
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def upsert_by_date(session: Session, model: Any, row_data: dict[str, Any]) -> None:
    """
    单行 upsert（按 date 字段作为冲突键）。

    参数：
    - session: SQLAlchemy 会话
    - model: 目标 ORM 模型类（比如 BtcPrice）
    - row_data: 一行字典数据，必须包含 date
    """
    if "date" not in row_data:
        raise ValueError("upsert_by_date 需要 row_data 包含 'date' 字段")

    stmt = sqlite_insert(model).values(**row_data)

    # 自动构造“更新字段”：除 date 外，其余字段都更新。
    update_cols = {col.name: stmt.excluded[col.name] for col in model.__table__.columns if col.name != "date"}

    stmt = stmt.on_conflict_do_update(index_elements=["date"], set_=update_cols)
    session.execute(stmt)


def bulk_upsert_by_date(session: Session, model: Any, rows: list[dict[str, Any]]) -> None:
    """
    批量 upsert（按 date 字段作为冲突键）。

    使用建议：
    - rows 可以是很多行数据
    - 外部控制 commit 时机（比如最后统一 commit）
    """
    if not rows:
        return

    for row in rows:
        upsert_by_date(session=session, model=model, row_data=row)
