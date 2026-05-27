"""
建表迁移：btc_bottom_score（抄底分数每日一条）。

用法（在仓库根目录）：
    python3 scripts/migrate_add_btc_bottom_score.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db.database import init_db


def main() -> None:
    init_db()
    print("[migrate] btc_bottom_score 表已就绪（create_all 幂等）。")


if __name__ == "__main__":
    main()
