"""
一次性清理旧版「预计算百分位」表与列（幂等）。

用法：
    python3 scripts/migrate_add_multiyear_percentiles.py

说明：
- 历史上本脚本曾用于 ADD COLUMN；现改为调用 apply_legacy_percentile_cleanup
- 与 init_db() 中执行的迁移一致，便于单独手工跑一遍
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许「python3 scripts/本文件.py」从仓库根目录执行时找到 app 包
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.db.database import init_db


def main() -> None:
    # init_db() 内部已执行 apply_legacy_percentile_cleanup（控制台会打印 [schema] ...）
    init_db()
    print("[migrate] 完成：与日常调度共用同一套 init_db（建表 + 清理旧百分位列/表）。")


if __name__ == "__main__":
    main()
