"""Phase 1.5.4 Task #4 — 从 finance.db 删除 `docx-empirical` chapter (含其 questions)。

硬约束:`实证研究结构框架(1).docx` 不属考试资料,必须从数据库剔除该资料的所有数据。

设计:
- 用 sqlite3 直连 (避免 SQLAlchemy ORM 隐藏 cascade 行为)。
- 流程:
    1. SELECT COUNT(*) FROM questions WHERE chapter_id=<empirical chapter>
    2. DELETE FROM questions WHERE chapter_id=<empirical chapter>
    3. DELETE FROM chapters WHERE code='docx-empirical'
    4. Verify SELECT COUNT(*) = 0

Usage:
    cd packages/backend && python -m packages.backend.scripts.remove_empirical_chapter
    # 或
    python packages/backend/scripts/remove_empirical_chapter.py --db data/final/finance.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # packages/backend/scripts/ → project root
DEFAULT_DB = PROJECT_ROOT / "data" / "final" / "finance.db"

EMPIRICAL_CHAPTER_CODE = "docx-empirical"


def remove_empirical(db_path: Path) -> dict:
    """从 finance.db 删除 docx-empirical chapter (含其 questions)。

    Returns: dict 包含操作前后计数 / 删除行数。
    """
    if not db_path.exists():
        raise FileNotFoundError(f"finance.db 不存在: {db_path}")

    # 显式 timeout 避免偶发 SQLITE_BUSY
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        # 1. 查 empirical chapter id
        chap_row = conn.execute(
            "SELECT id, subject_id, code, title FROM chapters WHERE code = ?",
            (EMPIRICAL_CHAPTER_CODE,),
        ).fetchone()

        if chap_row is None:
            print(f"INFO: {EMPIRICAL_CHAPTER_CODE} chapter 不存在,无需删除 (idempotent)")
            return {
                "chapter_id": None,
                "subject_id": None,
                "questions_before": 0,
                "questions_deleted": 0,
                "chapter_deleted": 0,
                "verified": True,
            }

        chapter_id = chap_row["id"]
        subject_id = chap_row["subject_id"]
        print(f"Found chapter: id={chapter_id}, subject_id={subject_id}, "
              f"code={chap_row['code']}, title={chap_row['title']}")

        # 2. 查 questions 数 (含 status 不论, 直接 delete 干净)
        q_before = conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()["n"]
        print(f"Questions in chapter before delete: {q_before}")

        # 3. DELETE questions → DELETE chapter (显式事务)
        cur = conn.execute("BEGIN")
        try:
            q_deleted = conn.execute(
                "DELETE FROM questions WHERE chapter_id = ?",
                (chapter_id,),
            ).rowcount
            c_deleted = conn.execute(
                "DELETE FROM chapters WHERE id = ?",
                (chapter_id,),
            ).rowcount
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise

        print(f"Deleted: questions={q_deleted}, chapters={c_deleted}")

        # 4. Verify
        chap_after = conn.execute(
            "SELECT COUNT(*) AS n FROM chapters WHERE code = ?",
            (EMPIRICAL_CHAPTER_CODE,),
        ).fetchone()["n"]
        q_after = conn.execute(
            """SELECT COUNT(*) AS n
               FROM questions q
               JOIN chapters c ON q.chapter_id = c.id
               WHERE c.code = ?""",
            (EMPIRICAL_CHAPTER_CODE,),
        ).fetchone()["n"]

        verified = chap_after == 0 and q_after == 0
        print(f"Verify: chapters_with_code={chap_after}, questions_with_code={q_after}, "
              f"verified={verified}")
        if not verified:
            raise RuntimeError(
                f"Verify 失败: chapters_with_code={chap_after}, questions_with_code={q_after}"
            )

        # 5. 整体 corp-strat 计数
        corp_total = conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE subject_id = ?",
            ("corp-strat",),
        ).fetchone()["n"]
        corp_chapters = conn.execute(
            "SELECT COUNT(*) AS n FROM chapters WHERE subject_id = ?",
            ("corp-strat",),
        ).fetchone()["n"]
        fin_total = conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE subject_id = ?",
            ("fin-mgmt",),
        ).fetchone()["n"]
        print(f"After: corp-strat questions={corp_total}, corp-strat chapters={corp_chapters}")
        print(f"After: fin-mgmt questions={fin_total} (must be unchanged: 565)")

        return {
            "chapter_id": chapter_id,
            "subject_id": subject_id,
            "questions_before": q_before,
            "questions_deleted": q_deleted,
            "chapter_deleted": c_deleted,
            "verified": verified,
            "corp_strat_questions_after": corp_total,
            "corp_strat_chapters_after": corp_chapters,
            "fin_mgmt_questions_after": fin_total,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="删除 finance.db 中 docx-empirical chapter (含其 questions)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"finance.db 路径 (default: {DEFAULT_DB})",
    )
    args = parser.parse_args(argv)

    try:
        result = remove_empirical(args.db.resolve())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
