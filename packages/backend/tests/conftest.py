"""共享 pytest fixtures — Phase 2-final。

fixtures:
- project_root: 项目根路径(从 tests/conftest.py 反推 2 层)
- finance_db_path: live data/final/finance.db 绝对路径
- live_corp_strat_count: live corp-strat 题数(session scope,缓存)
- live_fin_mgmt_count: live fin-mgmt 题数(session scope,缓存)
- live_corp_strat_chapters: live corp-strat chapter 数(session scope)

设计取舍(ponytail):
- scope="session" 缓存查询结果 — 跑一次 test suite 只查 1 次 DB
- 用 sync sqlite3 而不是 SQLAlchemy async — fixtures 简单不阻塞
- 缺失 finance.db → skip(防御性,避免 cross-environment 失败)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# tests/conftest.py → /home/.../EggEgg_Examination_System/packages/backend/tests/conftest.py
# .parent = tests/
# .parent.parent = packages/backend/
# .parent.parent.parent = packages/
# .parent.parent.parent.parent = 项目根(4 级向上)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FINANCE_DB_PATH = PROJECT_ROOT / "data" / "final" / "finance.db"

# 让 import 路径能引用 packages/backend(避免 hardcode sys.path)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(scope="session")
def project_root() -> Path:
    """项目根路径 /home/ljh2923/opencode-project/EggEgg_Examination_System/。"""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def finance_db_path(project_root: Path) -> Path:
    """live finance.db 绝对路径。"""
    return project_root / "data" / "final" / "finance.db"


def _query_count(db_path: Path, subject_id: str) -> int:
    """直读 SQLite 查 question count(同步、绕开 async 依赖)。"""
    if not db_path.exists():
        pytest.skip(f"finance.db 不存在: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE subject_id = ?", (subject_id,)
        )
        return cur.fetchone()[0]
    finally:
        conn.close()


@pytest.fixture(scope="session")
def live_corp_strat_count(finance_db_path: Path) -> int:
    """live corp-strat 题数。

    Phase 2-Lane-C 后预期:≥63 题(rerun 后)。
    旧 baseline(Phase 1.5.5 前):0 题。
    旧 baseline(Phase 1.5.5):20 题。
    Phase 2-final:实际为 63 题。
    测试用例用此 fixture 做 dynamic assertion,避免 hardcode "20"/"63" 失效。
    """
    return _query_count(finance_db_path, "corp-strat")


@pytest.fixture(scope="session")
def live_fin_mgmt_count(finance_db_path: Path) -> int:
    """live fin-mgmt 题数(预期 565,noise-removal 锁定不变)。"""
    return _query_count(finance_db_path, "fin-mgmt")


@pytest.fixture(scope="session")
def live_corp_strat_chapters(finance_db_path: Path) -> int:
    """live corp-strat chapter 数(预期 ≥2,Phase 2-Lane-B 注入)。"""
    if not finance_db_path.exists():
        pytest.skip(f"finance.db 不存在: {finance_db_path}")
    conn = sqlite3.connect(str(finance_db_path), timeout=10.0)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM chapters WHERE subject_id = ?", ("corp-strat",)
        )
        return cur.fetchone()[0]
    finally:
        conn.close()
