"""
初始化数据库脚本。

运行方式：
python -m app.scripts.init_db
"""

from app.db.base import Base, engine

# 导入模型，确保 Base 知道有哪些表要创建
from app.db import models  # noqa: F401


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("数据库初始化完成：index_monitor.db")


if __name__ == "__main__":
    main()
