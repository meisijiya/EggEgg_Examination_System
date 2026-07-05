"""insert_ai_approved_questions.py — 把 AI-approved 题注入 finance.db(append-only 模式)。

Phase 1.5.2 任务 #3:
- 读 data/parsed/corporate_strategy_ai_generated.jsonl(由 corporate_strategy_q_gen 产出)
  → 过滤 status='approved' 的行
- 注入到 data/final/finance.db 的 questions 表(subject_id='corp-strat')
- **不动** fin-mgmt 已有的 565 题 / 9 chapters(subject_id='fin-mgmt')
- **不动** 我之前 seed 的 corp-strat 11 chapters(subject_id='corp-strat')

为什么不用 build_db.py:
- build_db.py 设计是 "delete + recreate" 模式(Line 333-335 `db_path.unlink()`)
- 即使传 --subject corp-strat,line 442 `INSERT` 时仍硬编码 `"sid": "fin-mgmt"`
- 改 5-10 行 patch 仍可能副作用 → 走独立 sqlite3 直连 append 模式,零风险

实现要点:
- 使用 sqlite3 直连(简单,非 async,append-only 单 DB)
- INSERT OR IGNORE for subjects / chapters(幂等)
- INSERT for questions(无 id 唯一约束,id 自增)
- chapter 映射:AI JSONL 的 source_ref.file (DOCX 名) → 之前 seed 的 docx-* code
- 不修改 build_db.py / grader.py / paper_assembler / adapt_service / corporat_q_gen
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
FINAL_DIR = PROJECT_ROOT / "data" / "final"
DEFAULT_AI_JSONL = PARSED_DIR / "corporate_strategy_ai_generated.jsonl"
DEFAULT_FINANCE_DB = FINAL_DIR / "finance.db"
CORP_SUBJECT_ID = "corp-strat"
CORP_SUBJECT_NAME = "公司战略和风险管理"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("insert_ai_approved_questions")

# ---------------------------------------------------------------------------
# Chapter 映射 — AI JSONL source_ref.file → 我之前 seed 的 docx-* code
# ---------------------------------------------------------------------------

# 沿用 seed_corporate_strategy.py 的 DOCX_TITLE_SLUG(保持代码统一)
DOCX_FILE_TO_CODE: dict[str, str] = {
    "PEST分析案例资料(1).docx": "docx-pest",
    "企业战略(1).docx": "docx-corp",
    "实证研究结构框架(1).docx": "docx-empirical",
    "战略稳定性与文化适应性简答题(1).docx": "docx-stab-adapt",
    "战略选择与实施案例资料(1).docx": "docx-choice-impl",
    "探索战略创新的不同方面的主观题(1).docx": "docx-innovation-subj",
}

# DB 已枚举的合法 type(沿用 questions CHECK constraint)
ALLOWED_TYPES = frozenset({"single", "multi", "judge", "calc", "comprehensive"})


# ---------------------------------------------------------------------------
# AI JSONL chapter 解析
# ---------------------------------------------------------------------------


def _resolve_chapter_code(row: dict, chapter_id_cache: dict[str, int]) -> str | None:
    """从 AI JSONL 行解析 chapter code(从 cache 反查 chapter_id)。

    优先级:
      1. row.get("chapter") in chapter_id_cache
      2. row["source_ref"]["file"] → DOCX_FILE_TO_CODE → chapter_id_cache check

    找不到 → 返回 None(调用方标记 rejected)
    """
    # 优先级 1:row.chapter 直接给
    chap = row.get("chapter")
    if chap and chap in chapter_id_cache:
        return chap

    # 优先级 2:从 source_ref.file 映射
    src_file = (row.get("source_ref") or {}).get("file", "")
    if src_file in DOCX_FILE_TO_CODE:
        code = DOCX_FILE_TO_CODE[src_file]
        if code in chapter_id_cache:
            return code

    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _chapter_id_cache(conn: sqlite3.Connection, subject_id: str) -> dict[str, int]:
    """SELECT chapters WHERE subject_id=... → {code: chapter_id}。"""
    rows = conn.execute(
        "SELECT id, code FROM chapters WHERE subject_id = ?", (subject_id,)
    ).fetchall()
    return {row[1]: row[0] for row in rows}


def _ensure_subject(conn: sqlite3.Connection, subject_id: str, subject_name: str) -> None:
    """INSERT OR IGNORE INTO subjects(id, name)。幂等(已存在不报错)。"""
    conn.execute(
        "INSERT OR IGNORE INTO subjects (id, name) VALUES (?, ?)",
        (subject_id, subject_name),
    )


def _read_ai_jsonl(path: Path) -> list[dict]:
    """读 AI JSONL → list of dicts。空文件返回 []。"""
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("line %d JSON 解析失败: %s — skip", ln, e)
    return rows


def _check_finance_safe(conn: sqlite3.Connection) -> tuple[int, int]:
    """读 finance.db 当前 fin-mgmt / corp-strat 数据,返回 (fin-mgmt questions, corp-strat questions)。"""
    fin_n = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE subject_id='fin-mgmt'"
    ).fetchone()[0]
    corp_n = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE subject_id='corp-strat'"
    ).fetchone()[0]
    return fin_n, corp_n


def insert_approved_questions(
    ai_jsonl_path: Path,
    finance_db_path: Path,
    subject_id: str = CORP_SUBJECT_ID,
    subject_name: str = CORP_SUBJECT_NAME,
) -> dict:
    """插入 AI-approved 题到 finance.db(corp-strat subject,append-only)。

    Returns:
        stats dict {
          "total_in_jsonl", "approved_count", "inserted", "skipped_dup",
          "rejected_no_chapter", "rejected_bad_type", "rejected_missing_fields",
          "fin_mgmt_before", "fin_mgmt_after", "corp_strat_before", "corp_strat_after"
        }
    """
    rows = _read_ai_jsonl(ai_jsonl_path)
    total_in_jsonl = len(rows)

    # 1. 备份 finance.db 防御
    backup_path = finance_db_path.with_suffix(".db.bak-pre-insert-ai")
    if not backup_path.exists():
        import shutil
        shutil.copy2(finance_db_path, backup_path)
        logger.info("防御备份 → %s", backup_path)

    if not rows:
        logger.error("输入 JSONL 为空: %s", ai_jsonl_path)
        return {"total_in_jsonl": 0, "error": "empty_jsonl"}

    # 2. 连接 DB
    conn = sqlite3.connect(str(finance_db_path))
    try:
        fin_before, corp_before = _check_finance_safe(conn)
        logger.info(
            "DB 状态: fin-mgmt questions=%d, corp-strat questions=%d",
            fin_before, corp_before,
        )

        # 3. 确保 corp-strat subject 在
        _ensure_subject(conn, subject_id, subject_name)

        # 4. chapter 映射 cache
        chapter_map = _chapter_id_cache(conn, subject_id)
        logger.info("corp-strat chapters in DB: %d (codes=%s)",
                    len(chapter_map), sorted(chapter_map.keys()))

        if not chapter_map:
            logger.error(
                "corp-strat chapters 不存在 → 必须先跑 seed_corporate_strategy.py"
            )
            return {
                "total_in_jsonl": total_in_jsonl,
                "error": "no_chapters_for_corp_strat",
            }

        # 5. 过滤 approved + INSERT
        inserted = 0
        skipped_dup = 0
        rejected_no_chapter = 0
        rejected_bad_type = 0
        rejected_missing_fields = 0
        approved_seen = 0

        for row in rows:
            if row.get("status") != "approved":
                continue
            approved_seen += 1

            # 必填字段
            stem = (row.get("stem") or "").strip()
            answer = (row.get("answer") or "").strip()
            qtype = (row.get("type") or "").strip()

            if not stem or not answer or not qtype:
                rejected_missing_fields += 1
                continue

            if qtype not in ALLOWED_TYPES:
                logger.debug(
                    "skip non-allowed type=%s (id=%s) — finance.db CHECK constraint",
                    qtype, row.get("id"),
                )
                rejected_bad_type += 1
                continue

            chapter_code = _resolve_chapter_code(row, chapter_map)
            if chapter_code is None:
                rejected_no_chapter += 1
                continue
            chapter_id = chapter_map[chapter_code]

            # INSERT INTO questions
            try:
                options = row.get("options")
                options_json = (
                    json.dumps(options, ensure_ascii=False)
                    if isinstance(options, list) and options else None
                )
                key_points = row.get("key_points")
                key_points_json = (
                    json.dumps(key_points, ensure_ascii=False)
                    if isinstance(key_points, list) and key_points else None
                )
                analysis = row.get("analysis") or None

                # 幂等:同 id (来自 source_ref) 重复 INSERT 时仍可能 INSERT(因为 id 是 autoincrement,
                # 但若 id 显式提供相同 → PK conflict)。
                # AI JSONL 用 hash id 派生,不同段 id 应不同;这里用 INSERT 简单实现。
                conn.execute(
                    """
                    INSERT INTO questions (
                        subject_id, chapter_id, type, difficulty, stem,
                        options_json, answer, key_points_json, analysis,
                        source_pdf, page_ref
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        subject_id,        # corp-strat
                        chapter_id,
                        qtype,
                        int(row.get("difficulty") or 2),
                        stem,
                        options_json,
                        answer,
                        key_points_json,
                        analysis,
                        (row.get("source_ref") or {}).get("file", "ai_generated"),
                        (row.get("source_ref") or {}).get("paragraph_index", 1),
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError as e:
                logger.debug("dup insert skip (id=%s): %s", row.get("id"), e)
                skipped_dup += 1

        # 6. commit
        conn.commit()

        # 7. 验证 fin-mgmt 没动
        fin_after, corp_after = _check_finance_safe(conn)

        stats = {
            "total_in_jsonl": total_in_jsonl,
            "approved_count": approved_seen,
            "inserted": inserted,
            "skipped_dup": skipped_dup,
            "rejected_no_chapter": rejected_no_chapter,
            "rejected_bad_type": rejected_bad_type,
            "rejected_missing_fields": rejected_missing_fields,
            "fin_mgmt_before": fin_before,
            "fin_mgmt_after": fin_after,
            "corp_strat_before": corp_before,
            "corp_strat_after": corp_after,
        }
        return stats
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insert_ai_approved_questions",
        description=(
            "把 corp-strat AI-approved JSONL 注入 finance.db (append-only, 不动 fin-mgmt)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ai-jsonl",
        type=Path,
        default=DEFAULT_AI_JSONL,
        help="AI 出题 JSONL(status=approved 才入)",
    )
    parser.add_argument(
        "--finance-db",
        type=Path,
        default=DEFAULT_FINANCE_DB,
        help="目标 finance.db 路径",
    )
    parser.add_argument(
        "--subject-id",
        type=str,
        default=CORP_SUBJECT_ID,
        help="目标 subject_id(默认 = corp-strat)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    stats = insert_approved_questions(
        ai_jsonl_path=args.ai_jsonl,
        finance_db_path=args.finance_db,
        subject_id=args.subject_id,
        subject_name=CORP_SUBJECT_NAME,
    )

    if "error" in stats:
        logger.error("失败: %s", stats["error"])
        return 1

    # 报告
    logger.info("=" * 60)
    logger.info("insert_ai_approved 完成:")
    logger.info("  AI JSONL total:            %d", stats["total_in_jsonl"])
    logger.info("  approved(过滤后):          %d", stats["approved_count"])
    logger.info("  INSERT 成功:               %d", stats["inserted"])
    logger.info("  skip dup (id 冲突):        %d", stats["skipped_dup"])
    logger.info("  rejected 详情:")
    logger.info("    - 无 chapter 映射:       %d", stats["rejected_no_chapter"])
    logger.info("    - 不支持的 type:         %d", stats["rejected_bad_type"])
    logger.info("    - 缺 stem/answer/type:   %d", stats["rejected_missing_fields"])
    logger.info("  finance.db 状态:")
    logger.info("    - fin-mgmt questions:    %d → %d (UNCHANGED check)",
                stats["fin_mgmt_before"], stats["fin_mgmt_after"])
    logger.info("    - corp-strat questions:  %d → %d",
                stats["corp_strat_before"], stats["corp_strat_after"])
    if stats["fin_mgmt_before"] != stats["fin_mgmt_after"]:
        logger.error("❌ fin-mgmt 数据被意外修改!rollback建议")
        return 2
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
