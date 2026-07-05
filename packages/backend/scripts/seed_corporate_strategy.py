"""公司战略和风险管理 科目 seed 脚本 — INSERT corp-strat 到 finance.db。

Phase 1.3a Task 2 后端 part 2 (schema-only lane):
- 目标: data/final/finance.db(与现有 9 chapters / 565 questions 共享 ORM)
- 方式: SQLAlchemy 同步 session + SELECT-then-INSERT 幂等
- 零 Alembic migration(依赖 build_db.py 已建的 schema)
- chapters 数据来源优先级:
    1. --chapters-json: data/parsed/corporate_strategy_chapters.json(fix-30a 产出)
    2. docx JSONL source_files: data/parsed/corporate_strategy_questions_docx.jsonl
    3. 硬编码兜底(5 PDF + 6 DOCX = 11 章)
- CLI: cd packages/backend && python -m packages.backend.scripts.seed_corporate_strategy

执行流程:
    1. 解析 CLI(默认 db_url = sqlite:///./data/final/finance.db)
    2. 读 chapters spec(三档 fallback)
    3. ensure_sqlite_parent():SQLite URL 时确保父目录存在
    4. seed_subject():SELECT-then-INSERT(幂等)
    5. seed_chapters():SELECT-then-INSERT(每行独立幂等)
    6. report_counts():查各表行数 + corp-strat sub-count

约束(按 task spec):
- 不改 grader.py / adapt_service / paper_assembler / fixtures
- 不实施 Alembic migration
- 不动 finance 已有 fin-mgmt 数据(只新增 corp-strat subject + chapters)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# 让脚本作为 module 加载时能找到 app.models — Path 解析: scripts/ → packages/backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.database import Base  # noqa: E402
from app.models.question import Chapter, Subject  # noqa: E402

# ---------------------------------------------------------------------------
# 日志(模块级单例)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("seed_corporate_strategy")

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
FINAL_DIR = PROJECT_ROOT / "data" / "final"
DEFAULT_CHAPTERS_JSON = PARSED_DIR / "corporate_strategy_chapters.json"
DEFAULT_DB_PATH = FINAL_DIR / "finance.db"
DEFAULT_DOCX_JSONL = PARSED_DIR / "corporate_strategy_questions_docx.jsonl"
CORP_SRC_DIR = PROJECT_ROOT / "公司战略和风险管理"

# ---------------------------------------------------------------------------
# 兜底默认(若 chapters JSON + docx JSONL 都不存在)
# ---------------------------------------------------------------------------

CORP_STRAT_SUBJECT: dict[str, str] = {
    "id": "corp-strat",
    "name": "公司战略和风险管理",
}

# 5 个 PDF 章节(对应 公司战略和风险管理/第N章.pdf 共 5 个)
DEFAULT_PDF_CHAPTERS: list[dict[str, Any]] = [
    {"code": "pdf-ch1", "title": "战略与战略管理", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch2", "title": "战略分析", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch3", "title": "战略选择", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch4", "title": "战略实施", "weight": 1.0, "source_kind": "pdf"},
    {"code": "pdf-ch5", "title": "战略控制与风险管理", "weight": 1.0, "source_kind": "pdf"},
]

# docx → (中文 title, chapter code slug) 映射
# 用于：从 corporate_strategy_questions_docx.jsonl 的 source_files 派生 chapters
DOCX_TITLE_SLUG: dict[str, tuple[str, str]] = {
    "PEST分析案例资料(1).docx": ("PEST分析案例资料", "pest"),
    "企业战略(1).docx": ("企业战略案例", "corp"),
    "实证研究结构框架(1).docx": ("实证研究结构框架", "empirical"),
    "战略稳定性与文化适应性简答题(1).docx": ("战略稳定性与文化适应性（简答）", "stab-adapt"),
    "战略选择与实施案例资料(1).docx": ("战略选择与实施案例", "choice-impl"),
    "探索战略创新的不同方面的主观题(1).docx": ("探索战略创新的不同方面（主观题）", "innovation-subj"),
}

# 硬编码兜底 docx chapters(.jsonl 也没时的最后退路)
_HARDCODED_DOCX_CHAPTERS: list[dict[str, Any]] = [
    {"code": f"docx-{slug}", "title": title, "weight": 1.0, "source_kind": "docx"}
    for title, slug in DOCX_TITLE_SLUG.values()
]


# ---------------------------------------------------------------------------
# Chapters Spec 加载(三档 fallback)
# ---------------------------------------------------------------------------


def _normalize_chapter(raw: Any) -> dict[str, Any] | None:
    """规整化单章字典。缺 code/title → None(被过滤)。"""
    if not isinstance(raw, dict):
        return None
    code = raw.get("code")
    title = raw.get("title")
    if not code or not title:
        return None
    return {
        "code": str(code),
        "title": str(title),
        "weight": float(raw.get("weight", 1.0)),
        "source_kind": str(raw.get("source_kind", "unknown")),
    }


def load_chapter_spec(
    chapters_json_path: Path,
    docx_jsonl_path: Path,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """读 chapters spec(三档 fallback)。返回 (subject, chapters)。

    Fallback 顺序:
        1. chapters JSON 存在且合法 → 用 JSON 的 subject + chapters
        2. chapters JSON 不存在或空 → 从 docx JSONL source_files 派生 docx
           chapters(基线 = DEFAULT_PDF_CHAPTERS 5 章)
        3. docx JSONL 也不存在 → 硬编码兜底(5 PDF + 6 DOCX = 11 章)

    Subject 来源优先级:
        - JSON 存在 → JSON 的 subject
        - 否则 → CORP_STRAT_SUBJECT 兜底
    """
    # 1. 尝试 chapters JSON
    if chapters_json_path.exists():
        try:
            data = json.loads(chapters_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("chapters JSON 解析失败 (%s): %s → 用 fallback", chapters_json_path, e)
        else:
            subject = data.get("subject")
            if not isinstance(subject, dict) or not subject.get("id") or not subject.get("name"):
                logger.warning("chapters JSON 的 subject 不合法 → 用 CORP_STRAT_SUBJECT 兜底")
                subject = dict(CORP_STRAT_SUBJECT)
            chapters_raw = data.get("chapters") or []
            chapters = [c for c in (_normalize_chapter(x) for x in chapters_raw) if c]
            if chapters:
                logger.info(
                    "从 chapters JSON 读取 %d 章 (subject=%s)",
                    len(chapters),
                    subject["id"],
                )
                return subject, chapters
            logger.warning("chapters JSON 为空/全部非法 → 用 fallback")

    # 2. fallback: 派生 from docx JSONL source_files
    chapters: list[dict[str, Any]] = list(DEFAULT_PDF_CHAPTERS)
    n_docx = 0
    if docx_jsonl_path.exists():
        seen: set[str] = set()
        for line in docx_jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = obj.get("source_file") or ""
            if not src.endswith(".docx") or src in seen:
                continue
            if src in DOCX_TITLE_SLUG:
                title, slug = DOCX_TITLE_SLUG[src]
                chapters.append({
                    "code": f"docx-{slug}",
                    "title": title,
                    "weight": 1.0,
                    "source_kind": "docx",
                })
                n_docx += 1
            seen.add(src)
        if n_docx > 0:
            logger.info("从 %s 派生 %d docx 章", docx_jsonl_path, n_docx)
        else:
            logger.warning(
                "%s 中未匹配 docx 行,使用硬编码兜底", docx_jsonl_path,
            )
            chapters.extend(_HARDCODED_DOCX_CHAPTERS)
    else:
        logger.warning(
            "docx JSONL 不存在 (%s),使用硬编码兜底 → %d 章",
            docx_jsonl_path,
            len(chapters) + len(_HARDCODED_DOCX_CHAPTERS),
        )
        chapters.extend(_HARDCODED_DOCX_CHAPTERS)
    return dict(CORP_STRAT_SUBJECT), chapters


# ---------------------------------------------------------------------------
# Seed Functions(幂等 INSERT)
# ---------------------------------------------------------------------------


def seed_subject(
    session_factory: Callable[[], Session],
    subject: dict[str, str],
) -> int:
    """INSERT subject 幂等。返回 inserted(0 或 1)。

    幂等策略:SELECT 先查 → None 才 INSERT。
    """
    sid = subject["id"]
    sname = subject["name"]
    with session_factory() as session:
        existing = session.execute(
            select(Subject).where(Subject.id == sid)
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("subject 已存在: id=%s, skip INSERT", sid)
            return 0
        session.add(Subject(id=sid, name=sname))
        session.commit()
        logger.info("subject INSERT: id=%s, name=%s", sid, sname)
        return 1


def seed_chapters(
    session_factory: Callable[[], Session],
    subject_id: str,
    chapters: list[dict[str, Any]],
) -> int:
    """INSERT chapters 幂等。返回 inserted 总数(0..N)。

    幂等策略:每行 SELECT (subject_id, code) → None 才 INSERT。
    """
    inserted = 0
    skipped = 0
    with session_factory() as session:
        for ch in chapters:
            existing = session.execute(
                select(Chapter).where(
                    Chapter.subject_id == subject_id,
                    Chapter.code == ch["code"],
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue
            session.add(Chapter(
                subject_id=subject_id,
                code=ch["code"],
                title=ch["title"],
                weight=float(ch.get("weight", 1.0)),
            ))
            inserted += 1
        if inserted > 0:
            session.commit()
            logger.info("chapters INSERT: %d 行 (skipped %d 已存在)", inserted, skipped)
        else:
            logger.info("所有 %d 章已存在, skip", skipped)
    return inserted


# ---------------------------------------------------------------------------
# 报告 + CLI
# ---------------------------------------------------------------------------


def report_counts(engine: Engine) -> dict[str, int]:
    """查 subjects / chapters 表总行数 + corp-strat 子集行数。"""
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        counts["subjects_total"] = int(
            conn.execute(text("SELECT COUNT(*) FROM subjects")).scalar() or 0
        )
        counts["chapters_total"] = int(
            conn.execute(text("SELECT COUNT(*) FROM chapters")).scalar() or 0
        )
        counts["subjects_corp_strat"] = int(
            conn.execute(text("SELECT COUNT(*) FROM subjects WHERE id='corp-strat'")).scalar() or 0
        )
        counts["chapters_corp_strat"] = int(
            conn.execute(
                text("SELECT COUNT(*) FROM chapters WHERE subject_id='corp-strat'")
            ).scalar() or 0
        )
    return counts


def _ensure_sqlite_parent(db_url: str) -> None:
    """SQLite URL 时确保父目录存在(rel 转 abs 用 PROJECT_ROOT)。"""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return
    path_part = db_url[len(prefix):]
    # 去掉可能的 query 参数(aiosqlite 用 aiosqlite sqlite:///path 不带 query)
    if "?" in path_part:
        path_part = path_part.split("?", 1)[0]
    if not path_part.startswith("/"):
        path_part = str(PROJECT_ROOT / path_part)
    Path(path_part).parent.mkdir(parents=True, exist_ok=True)


def _build_arg_parser() -> argparse.ArgumentParser:
    """CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="seed_corporate_strategy",
        description=(
            "公司战略和风险管理 科目 seed 脚本 — "
            "INSERT corp-strat subject + chapters 到 finance.db (幂等, 零 Alembic)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=f"sqlite:///{DEFAULT_DB_PATH}",
        help="SQLAlchemy DB URL(默认 = sqlite:///data/final/finance.db)",
    )
    parser.add_argument(
        "--chapters-json",
        type=Path,
        default=DEFAULT_CHAPTERS_JSON,
        help="chapters JSON 路径(fix-30a 输出;不存在则 fallback 到 docx JSONL)",
    )
    parser.add_argument(
        "--docx-jsonl",
        type=Path,
        default=DEFAULT_DOCX_JSONL,
        help="docx JSONL 路径(2 档 fallback,用于派生 docx chapters)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    使用方式:
        # 默认:写入 data/final/finance.db(共享题库)
        python -m packages.backend.scripts.seed_corporate_strategy
        # 写测试 DB
        python -m packages.backend.scripts.seed_corporate_strategy \\
            --db-url sqlite:////tmp/test.db
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    db_url: str = args.db_url
    _ensure_sqlite_parent(db_url)
    logger.info("目标数据库: %s", db_url)

    subject, chapters = load_chapter_spec(args.chapters_json, args.docx_jsonl)
    logger.info(
        "subject: id=%s, name=%s | chapters: %d (codes=%s)",
        subject["id"],
        subject["name"],
        len(chapters),
        [ch["code"] for ch in chapters],
    )

    engine = create_engine(db_url, echo=False, future=True)
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    n_subj = seed_subject(SessionFactory, subject)
    n_chap = seed_chapters(SessionFactory, subject["id"], chapters)
    counts = report_counts(engine)

    logger.info(
        "seed 完成: subject_inserted=%d, chapters_inserted=%d | "
        "subjects_total=%d (corp-strat=%d), chapters_total=%d (corp-strat=%d)",
        n_subj,
        n_chap,
        counts["subjects_total"],
        counts["subjects_corp_strat"],
        counts["chapters_total"],
        counts["chapters_corp_strat"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
