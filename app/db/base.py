"""
兼容层：保留旧文件路径，内部转发到 database.py。

你可以逐步把其他模块里的导入改成：
from app.db.database import Base, SessionLocal, engine
"""

from app.db.database import Base, SessionLocal, engine
