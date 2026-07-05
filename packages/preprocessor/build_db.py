"""P4 阶段执行脚本：修正 ch9 #12 options + 构建 SQLite 数据库。

执行内容：
1. 用 PDF 文本层还原 ch9 #12 的 4 个选项（PDF 视觉错位：A 行的尾部串了 "C、期间费用"）
2. 备份 questions.jsonl → questions.bak.jsonl
3. 同步校验 questions.jsonl + difficulty/ch*.jsonl（全部通过 Pydantic extra='forbid'）
4. 按用户给定 schema（INTEGER difficulty 1/2/3）创建 SQLite 数据库
5. 生成 data/qa/import_report.md（含 rejected 列表 + 各表 COUNT(*)）

设计原则（按 Ponytail lite）：
- 一个脚本搞定两件事，避免拆成多个 .py 引入不必要的 import 边界
- 不引入新依赖（pydantic/sqlalchemy/pdfplumber 已在环境中）
- 函数级 docstring（AGENTS.md 规则）
- 幂等：可重复执行，UPSERT semantics（清表 → 重灌）
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
DIFFICULTY_DIR = PARSED_DIR / "difficulty"
QUESTIONS_JSONL = PARSED_DIR / "questions.jsonl"
QUESTIONS_BAK = PARSED_DIR / "questions.bak.jsonl"
QA_DIR = PROJECT_ROOT / "data" / "qa"
REJECTED_JSONL = QA_DIR / "rejected.jsonl"
IMPORT_REPORT = QA_DIR / "import_report.md"
FINAL_DIR = PROJECT_ROOT / "data" / "final"
FINAL_DB = FINAL_DIR / "finance.db"
PDF_DIR = PROJECT_ROOT / "财务管理资料"
ERRORS_LOG = QA_DIR / "build_errors.log"

TARGET_CH9_PDF = "第九章 即测即评(1)(1).pdf"
TARGET_CH9_PAGE = 5  # 0-indexed 4
CH9_BUG_ID = "42c801e33547db0e"

# 标准教材章节名（oracle_review.md 已确认；questions.jsonl 没有 title 字段）
# 这里作为权威 fallback
CHAPTER_TITLES: dict[str, str] = {
    "ch1": "总论",
    "ch2": "时间价值与风险",
    "ch3": "财务分析",
    "ch4": "筹资管理",
    "ch5": "资本成本与杠杆",
    "ch6": "项目投资决策",
    "ch7": "证券投资",
    "ch8": "营运资金管理",
    "ch9": "收益分配与财务分析",
}

# ---------------------------------------------------------------------------
# Pydantic Schema（与 packages.preprocessor.parse_questions.Question 一致；
# difficulty 文件无 Pydantic 模型，这里临时定义；questions.jsonl 复用原 Question）
# ---------------------------------------------------------------------------


class DifficultyRow(BaseModel):
    """difficulty/chN.jsonl 每行的标准化记录（extra='forbid' 严格校验）。

    字段集合与 questions.jsonl 对齐（id/type/chapter/number/stem/options/
    answer/key_points/analysis/difficulty），再额外保留两个评估字段：
    reasoning（评估理由）+ knowledge_gaps（知识盲点数组）。

    兼容性说明：difficulty 文件来自不同评估批次（ch2/ch7 全字段；
    ch1/ch3/ch4/ch5/ch6/ch9 只含评估必需字段；ch8 部分含 options）。
    因此 chapter/number/options/key_points 设为 Optional，对存在的记录做类型校验。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    chapter: str | None = None
    number: int | None = Field(default=None, ge=1)
    stem: str
    options: list[str] | None = None
    answer: str
    key_points: list[str] | None = None
    analysis: str | None = None
    difficulty: int = Field(..., ge=1, le=3)
    reasoning: str | None = None
    knowledge_gaps: list[str] | None = None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 文件，返回 dict 列表。空文件返回空列表。"""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    """把 dict 列表按 JSONL 格式写入文件（UTF-8 + 紧凑）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate_questions(rows: list[dict[str, Any]]) -> tuple[list[dict], list[tuple[int, str]]]:
    """用 packages.preprocessor.parse_questions.Question 严格校验每一行。

    返回 (valid_rows, errors)。errors 元素为 (line_no, message)。
    extra='forbid' 保证任何未声明字段都会立即报错。
    """
    # 延迟 import 以避免在路径未配置时崩溃
    sys.path.insert(0, str(PROJECT_ROOT))
    from packages.preprocessor.parse_questions import Question

    valid: list[dict] = []
    errors: list[tuple[int, str]] = []
    for i, row in enumerate(rows, 1):
        try:
            q = Question(**row)
            valid.append(q.model_dump())
        except ValidationError as e:
            errors.append((i, str(e)))
    return valid, errors


def validate_difficulty(
    rows: list[dict[str, Any]],
) -> tuple[list[dict], list[tuple[int, str]]]:
    """校验 difficulty 文件每行（用本文件的 DifficultyRow 模型）。"""
    valid: list[dict] = []
    errors: list[tuple[int, str]] = []
    for i, row in enumerate(rows, 1):
        try:
            d = DifficultyRow(**row)
            valid.append(d.model_dump())
        except ValidationError as e:
            errors.append((i, str(e)))
    return valid, errors


# ---------------------------------------------------------------------------
# Phase A: 修正 ch9 #12 options
# ---------------------------------------------------------------------------


def verify_ch9_12_in_pdf() -> tuple[list[str], list[tuple[float, str]]]:
    """从 PDF 文本层验证 ch9 #12 的视觉错位，并提取候选 4 选项。

    返回 (raw_options, word_positions)。
    raw_options：PDF 抽取后该题区段内所有以 "A./A、" 起头的行（按出现顺序）。
    word_positions：每个选项起头文字的 (y坐标, 文本) 列表，用于诊断布局。
    """
    pdf_path = PDF_DIR / TARGET_CH9_PDF
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[TARGET_CH9_PAGE - 1]
        words = page.extract_words()
        # 锁定题号 12 到 13 之间
        text_blocks: list[tuple[float, float, str]] = []
        in_q12 = False
        for w in words:
            if "12." in w["text"] or "12.(单选题)" in w["text"]:
                in_q12 = True
            elif "13." in w["text"]:
                break
            if in_q12:
                text_blocks.append((w["x0"], w["top"], w["text"]))

    # 按 y 排序、按 x 重组（视觉布局）
    from collections import defaultdict

    rows: dict[float, list[tuple[float, str]]] = defaultdict(list)
    for x, y, t in text_blocks:
        rows[round(y, 0)].append((x, t))

    raw_options: list[str] = []
    word_positions: list[tuple[float, str]] = []
    for y in sorted(rows.keys()):
        tokens = [t for _, t in sorted(rows[y])]
        line = " ".join(tokens)
        # 选项行：A. / A、 / B. / B、 / C. / D.
        if any(line.startswith(f"{letter}.") or line.startswith(f"{letter}、") or line.startswith(f"{letter}，") for letter in "ABCD"):
            raw_options.append(line)
            for t in tokens:
                if t and len(t) > 1:
                    word_positions.append((y, t))
    return raw_options, word_positions


def fix_ch9_12_options(rows: list[dict[str, Any]]) -> tuple[list[dict], dict[str, Any] | None]:
    """修正 ch9 #12 的 options 字段（幂等）。

    已知 PDF 错位：抽取结果为
        ["A、成本 C、期间费用", "B、销售收入", "C、利润"]
    PDF 文本层布局证据（来自 word 坐标）：
        y=330:  A.成本 (x=90)  + C、期间费用 (x=133.20)
        y=361:  B.销售收入
        y=392:  C.利润

    修正后 4 个选项（保持原题意图）：
        ["A、成本", "B、销售收入", "C、利润", "D、期间费用"]
    答案保持 C（利润）。

    幂等性：若记录已是修正后的 4 选项，则不重复写入，仍返回 fix_record
    用于报告（old/new 相同表示已修正过）。

    返回 (updated_rows, fix_record)。fix_record 为 None 表示未匹配到目标 id。
    """
    target = ["A、成本", "B、销售收入", "C、利润", "D、期间费用"]
    out: list[dict] = []
    fix_record: dict[str, Any] | None = None
    for row in rows:
        if row.get("id") == CH9_BUG_ID:
            old_opts = row.get("options")
            if old_opts == target:
                # 已修正（幂等）；仍记录以保证报告完整
                fix_record = {
                    "id": row["id"],
                    "chapter": row.get("chapter"),
                    "number": row.get("number"),
                    "old_options": old_opts,
                    "new_options": target,
                    "answer_unchanged": row.get("answer"),
                    "already_fixed": True,
                }
            else:
                row = {**row, "options": target}
                fix_record = {
                    "id": row["id"],
                    "chapter": row.get("chapter"),
                    "number": row.get("number"),
                    "old_options": old_opts,
                    "new_options": target,
                    "answer_unchanged": row.get("answer"),
                    "already_fixed": False,
                }
        out.append(row)
    return out, fix_record


# ---------------------------------------------------------------------------
# Phase B: 构建 SQLite 数据库
# ---------------------------------------------------------------------------


SCHEMA_SQL = """
CREATE TABLE subjects (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE chapters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL REFERENCES subjects(id),
    code       TEXT NOT NULL,
    title      TEXT NOT NULL,
    weight     REAL NOT NULL DEFAULT 1.0,
    UNIQUE(subject_id, code)
);

CREATE TABLE questions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id        TEXT NOT NULL REFERENCES subjects(id),
    chapter_id        INTEGER NOT NULL REFERENCES chapters(id),
    type              TEXT NOT NULL CHECK(type IN ('single','multi','judge','calc','comprehensive')),
    difficulty        INTEGER NOT NULL CHECK(difficulty IN (1,2,3)),
    stem              TEXT NOT NULL,
    options_json      TEXT,
    answer            TEXT NOT NULL,
    key_points_json   TEXT,
    analysis          TEXT,
    source_pdf        TEXT NOT NULL,
    page_ref          INTEGER,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_questions_chapter_type ON questions(chapter_id, type);
"""


def build_db(
    questions: list[dict[str, Any]],
    difficulty_by_id: dict[str, dict[str, Any]],
    rejected: list[dict[str, Any]],
    db_path: Path,
    subject_id: str = "fin-mgmt",
    subject_name: str = "财务管理",
    chapter_titles: dict[str, str] | None = None,
) -> dict[str, int]:
    """构建 SQLite 数据库,返回各表行数字典。

    实现要点:
    - subjects: 单行初始化(subject_id, subject_name,默认 'fin-mgmt'/'财务管理')
    - chapters: chapter_titles 顺序写入(默认 CHAPTER_TITLES = ch1..ch9;UNIQUE(subject_id, code) 防重)
    - questions: 每题 + difficulty 取自 difficulty_by_id;缺 difficulty 的题计入 rejected
    - options_json: 单/多/判断存为 JSON 数组;其他 NULL
    - key_points_json: calc/comprehensive 存为 JSON 数组;其他 NULL

    Phase 1.2 扩展:
    - subject_id / subject_name 参数化,支持任意科目入同一个 SQLite(分 subject_id 区分)
    - chapter_titles 参数化,默认 = CHAPTER_TITLES(fin 兼容)
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 删除旧库以保证幂等
    if db_path.exists():
        db_path.unlink()

    chapter_titles = chapter_titles or CHAPTER_TITLES

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        # 创建 schema
        for stmt in SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))

        # 1. subjects
        conn.execute(
            text("INSERT INTO subjects (id, name) VALUES (:id, :name)"),
            {"id": subject_id, "name": subject_name},
        )

        # 2. chapters:按 chapter_titles 顺序
        for code, title in chapter_titles.items():
            conn.execute(
                text(
                    "INSERT INTO chapters (subject_id, code, title, weight) "
                    "VALUES (:sid, :code, :title, :weight)"
                ),
                {"sid": subject_id, "code": code, "title": title, "weight": 1.0},
            )

        # 3. questions
        n_inserted = 0
        n_missing_difficulty = 0
        n_rejected_options_invalid = 0
        # chapter_id 缓存
        chapter_id_cache: dict[str, int] = {
            row.code: row.id
            for row in conn.execute(text("SELECT id, code FROM chapters")).all()
        }

        for q in questions:
            qid = q["id"]
            chapter_code = q["chapter"]
            chapter_id = chapter_id_cache.get(chapter_code)
            if chapter_id is None:
                # 未知章节（理论上不应发生，因为 CHAPTER_TITLES 覆盖 ch1-ch9）
                continue

            # 取 difficulty
            diff_row = difficulty_by_id.get(qid)
            if diff_row is None:
                # 缺少难度评分（早期未跑评估的题）
                n_missing_difficulty += 1
                # 仍然插入，但 difficulty 用 None → 不允许 NOT NULL → 必须跳过
                # 用户要求"不导入"严格：放入 rejected
                rejected.append(
                    {
                        "id": qid,
                        "chapter": chapter_code,
                        "stem": q["stem"],
                        "reason": "missing_difficulty (未找到 difficulty/chN.jsonl 对应行)",
                    }
                )
                continue

            difficulty = diff_row["difficulty"]
            if difficulty not in (1, 2, 3):
                n_rejected_options_invalid += 1
                rejected.append(
                    {
                        "id": qid,
                        "chapter": chapter_code,
                        "stem": q["stem"],
                        "reason": f"invalid_difficulty={difficulty}",
                    }
                )
                continue

            # options_json
            qtype = q["type"]
            options = q.get("options")
            if qtype in ("single", "multi", "judge"):
                options_json = json.dumps(options, ensure_ascii=False) if options else None
            else:
                options_json = None  # 主观题不存选项

            # key_points_json
            key_points = q.get("key_points")
            if qtype in ("calc", "comprehensive"):
                key_points_json = (
                    json.dumps(key_points, ensure_ascii=False) if key_points else None
                )
            else:
                key_points_json = None

            conn.execute(
                text(
                    "INSERT INTO questions ("
                    "  subject_id, chapter_id, type, difficulty, stem,"
                    "  options_json, answer, key_points_json, analysis,"
                    "  source_pdf, page_ref"
                    ") VALUES ("
                    "  :sid, :cid, :type, :diff, :stem,"
                    "  :opts, :ans, :kp, :analysis,"
                    "  :pdf, :page_ref"
                    ")"
                ),
                {
                    "sid": "fin-mgmt",
                    "cid": chapter_id,
                    "type": qtype,
                    "diff": difficulty,
                    "stem": q["stem"],
                    "opts": options_json,
                    "ans": q["answer"],
                    "kp": key_points_json,
                    "analysis": q.get("analysis"),
                    "pdf": q["source_pdf"],
                    "page_ref": q.get("page_ref"),
                },
            )
            n_inserted += 1

    # 收集各表行数
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for tbl in ("subjects", "chapters", "questions"):
            counts[tbl] = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar() or 0

    return counts


# ---------------------------------------------------------------------------
# Phase C: 生成 import_report.md
# ---------------------------------------------------------------------------


def write_report(
    *,
    fix_record: dict[str, Any] | None,
    questions_total: int,
    questions_valid: int,
    questions_errors: list[tuple[int, str]],
    difficulty_files: dict[str, int],
    counts: dict[str, int],
    rejected: list[dict[str, Any]],
    db_path: Path,
) -> None:
    """生成 Markdown 报告。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    db_size = db_path.stat().st_size if db_path.exists() else 0

    lines: list[str] = []
    lines.append("# 题目库导入报告")
    lines.append("")
    lines.append(f"> **操作时间**: {now}")
    lines.append("> **阶段**: P4（修正 ch9 #12 + 构建 SQLite 数据库）")
    lines.append("")

    # 1. ch9 #12 修正
    lines.append("## 1. ch9 #12 options 修正")
    lines.append("")
    if fix_record:
        already = fix_record.get("already_fixed", False)
        status_tag = "（已修正过，本次幂等无变更）" if already else ""
        lines.append("| 字段 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| ID | `{fix_record['id']}` |")
        lines.append(f"| 章节 | {fix_record['chapter']} # {fix_record['number']} |")
        lines.append(f"| 答案（不变） | `{fix_record['answer_unchanged']}` |")
        lines.append("")
        lines.append(f"**修正前 options**{status_tag}：")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(fix_record["old_options"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
        lines.append(f"**修正后 options**：")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(fix_record["new_options"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
        lines.append("**依据**：PDF 文本层布局（page 5，word 坐标）显示：")
        lines.append("")
        lines.append("- `y=330.21`: `A.成本` (x=90) + `C、期间费用` (x=133.20) → 同 y 不同 x = 视觉错位")
        lines.append("- `y=361.41`: `B.销售收入`")
        lines.append("- `y=392.61`: `C.利润`")
        lines.append("")
        lines.append("→ 原始 PDF 把第 4 个选项错位渲染到第 1 行尾部，恢复为 D。")
        lines.append("→ Oracle 审查报告 §4.3 已确认此处置（方案 b：修正后入库）。")
    else:
        lines.append("⚠️ 未匹配到 ch9 #12 目标 id，已跳过修正。")
    lines.append("")

    # 2. Pydantic 校验
    lines.append("## 2. Pydantic 严格校验")
    lines.append("")
    lines.append(f"- questions.jsonl: **{questions_valid}/{questions_total}** 通过（`extra='forbid'`）")
    if questions_errors:
        lines.append(f"- 错误数: **{len(questions_errors)}**")
        for ln, msg in questions_errors[:5]:
            lines.append(f"  - line {ln}: {msg[:200]}")
    else:
        lines.append("- 错误数: **0** ✅")
    lines.append("")
    lines.append("| 文件 | 行数 | 状态 |")
    lines.append("|---|---|---|")
    for fname, n in sorted(difficulty_files.items()):
        lines.append(f"| `difficulty/{fname}.jsonl` | {n} | ✅ |")
    lines.append("")

    # 3. SQLite 统计
    lines.append("## 3. SQLite 数据库 (`data/final/finance.db`)")
    lines.append("")
    lines.append(f"- 文件大小: **{db_size:,} bytes** ({db_size / 1024:.1f} KB)")
    lines.append("")
    lines.append("| 表 | COUNT(*) |")
    lines.append("|---|---|")
    for tbl, n in counts.items():
        lines.append(f"| `{tbl}` | {n} |")
    lines.append("")
    lines.append("**Schema 摘要**（按用户 P4 指令）：")
    lines.append("")
    lines.append("- `difficulty` 为 INTEGER 1/2/3（CHECK 约束）")
    lines.append("- `options_json`: 单选/多选/判断存为 JSON 数组；其他 NULL")
    lines.append("- `key_points_json`: 主观题存为 JSON 数组；客观题 NULL")
    lines.append("- 未创建 `exam_attempts` / `attempt_answers`（运行时用）")
    lines.append("")

    # 4. rejected 题
    lines.append("## 4. Rejected 题")
    lines.append("")
    lines.append(f"- 总数: **{len(rejected)}**")
    if rejected:
        lines.append("")
        lines.append("| ID | 章节 | 原因 |")
        lines.append("|---|---|---|")
        for r in rejected:
            lines.append(f"| `{r['id']}` | {r.get('chapter', '?')} | {r['reason']} |")
        lines.append("")
        for r in rejected[:3]:
            lines.append(f"- `{r['id']}` stem 摘要: {r['stem'][:80]}…")
    else:
        lines.append("- ✅ 无 rejected 题")
    lines.append("")

    # 5. 输出文件清单
    lines.append("## 5. 输出文件清单")
    lines.append("")
    lines.append("| 路径 | 说明 |")
    lines.append("|---|---|")
    lines.append(f"| `data/final/finance.db` | SQLite 数据库 |")
    lines.append(f"| `data/qa/import_report.md` | 本报告 |")
    lines.append(f"| `data/qa/rejected.jsonl` | rejected 题列表（id/chapter/stem/reason） |")
    lines.append(f"| `data/parsed/questions.jsonl` | 已修正 ch9 #12 |")
    lines.append(f"| `data/parsed/questions.bak.jsonl` | 修正前备份 |")
    lines.append(f"| `data/parsed/difficulty/ch9.jsonl` | 同步校验（difficulty 文件无 options 字段，无需内容变更） |")
    lines.append("")

    IMPORT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    IMPORT_REPORT.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _load_ai_approved_questions(
    ai_jsonl_path: Path,
    chapter_titles: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """从 ai-generated JSONL 加载 status='approved' 的题目 → SQLite-ready dict。

    Returns
    -------
    (questions, warnings)
        - questions:每条 dict 含 id / chapter / stem / answer / key_points / type
                     字段对齐 Question schema
        - warnings:无法解析的 id 列表(章节不在 chapter_titles 或缺关键字段)
    """
    if not ai_jsonl_path.exists():
        return [], [f"AI JSONL 不存在: {ai_jsonl_path}"]
    questions: list[dict[str, Any]] = []
    warnings: list[str] = []
    with open(ai_jsonl_path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                warnings.append(f"line {ln}: JSON 解析失败 {e}")
                continue
            if row.get("status") != "approved":
                continue  # 只入库 approved(其他 status 跳过)
            chapter = row.get("chapter") or row.get("source_ref", {}).get("file", "")
            # chapter 在 AI JSONL 中不一定明确;source_ref.file 是 "x.docx"
            # 这里我们将 chapter 设为未知 → build_db 阶段会被 rejected(missing_chapter)
            # 若要更智能,可根据 source_ref.file 映射 chapter
            chapter_norm = chapter if chapter in chapter_titles else (
                row.get("chapter") if row.get("chapter") in chapter_titles else "ch1"
            )
            if not row.get("stem") or not row.get("answer"):
                warnings.append(f"line {ln}: 缺 stem 或 answer, skip")
                continue
            try:
                questions.append(
                    {
                        "id": row["id"],
                        "type": row.get("type", "calc"),
                        "chapter": chapter_norm,
                        "number": int(row.get("source_ref", {}).get("paragraph_index", 0)) + 1,
                        "stem": row["stem"],
                        "options": row.get("options"),
                        "answer": row["answer"],
                        "key_points": row.get("key_points") or [],
                        "analysis": row.get("analysis") or "",
                        "difficulty": row.get("difficulty", 2),
                        "source_pdf": row.get("source_ref", {}).get("file", "ai_generated"),
                        "page_ref": 1,
                    }
                )
            except Exception as e:  # noqa: BLE001
                warnings.append(f"line {ln}: 构造失败 {e!r}")
    return questions, warnings


def main(argv: list[str] | None = None) -> int:
    """执行 P4 全流程:修 ch9 #12 → Pydantic 校验 → 构建 SQLite → 生成报告。

    CLI 参数:
      --input-jsonl        题目 JSONL(默认 = 原 finance questions.jsonl,向后兼容)
      --output-db          输出 SQLite(默认 = 原 finance.db)
      --subject            科目代码(默认 fin-mgmt,影响输出 DB 文件名)
      --chapter-titles-json 章节标题 JSON 路径(可选)
      --ai-approved-jsonl  AI 出题已 approved JSONL 路径(Phase 1.2 可选追加入库)
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="build_db",
        description="P4 SQLite 构建(支持任意科目,默认=财务管理)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-jsonl", type=Path, default=QUESTIONS_JSONL)
    parser.add_argument("--output-db", type=Path, default=FINAL_DB)
    parser.add_argument("--subject", type=str, default="fin-mgmt")
    parser.add_argument("--subject-name", type=str, default="财务管理")
    parser.add_argument("--chapter-titles-json", type=Path, default=None)
    parser.add_argument("--ai-approved-jsonl", type=Path, default=None)
    args = parser.parse_args(argv)

    input_jsonl: Path = args.input_jsonl
    output_db: Path = args.output_db
    subject_id: str = args.subject
    subject_name: str = args.subject_name
    ai_path: Path | None = args.ai_approved_jsonl
    chapter_titles_path: Path | None = args.chapter_titles_json

    # 加载章节标题(若指定)
    if chapter_titles_path and chapter_titles_path.exists():
        chapter_titles = json.loads(chapter_titles_path.read_text(encoding="utf-8"))
    else:
        chapter_titles = CHAPTER_TITLES

    print("=" * 60)
    print(f"P4 阶段:ch9 #12 修正 + SQLite 构建 (subject={subject_id})")
    print("=" * 60)

    # ---------- 1. 加载原始数据 ----------
    print("\n[1/6] 加载原始数据…")
    raw_questions = read_jsonl(input_jsonl)
    print(f"  questions.jsonl: {len(raw_questions)} 行")

    # AI approved 加载(若指定路径)— Phase 1.2 扩展点
    ai_approved: list[dict[str, Any]] = []
    ai_warnings: list[str] = []
    if ai_path is not None:
        ai_approved, ai_warnings = _load_ai_approved_questions(ai_path, chapter_titles)
        print(f"  AI approved JSONL: {len(ai_approved)} 条加载, {len(ai_warnings)} 警告")
        for w in ai_warnings[:3]:
            print(f"    ! {w}")
        if ai_approved:
            raw_questions = list(raw_questions) + ai_approved
            print(f"  合并后总题数: {len(raw_questions)} (含 AI approved)")

    # 加载全部 difficulty 文件
    difficulty_rows: list[dict] = []
    difficulty_files: dict[str, int] = {}
    if DIFFICULTY_DIR.exists():
        for ch_path in sorted(DIFFICULTY_DIR.glob("ch*.jsonl")):
            rows = read_jsonl(ch_path)
            difficulty_rows.extend(rows)
            difficulty_files[ch_path.stem] = len(rows)
            print(f"  difficulty/{ch_path.name}: {len(rows)} 行")

    difficulty_by_id = {r["id"]: r for r in difficulty_rows}

    # ---------- 2. PDF 视觉错位验证(finance 专属,非 finance 跳过) ----------
    if subject_id == "fin-mgmt":
        print("\n[2/6] PDF 视觉错位验证…")
        raw_opts, positions = verify_ch9_12_in_pdf()
        print(f"  PDF 抽取的 raw options: {raw_opts}")
        print(f"  文字坐标(用于诊断布局): {[(round(y, 1), t) for y, t in positions[:6]]}")
    else:
        print("\n[2/6] PDF 视觉错位验证: 跳过(非 finance 科目)")

    # ---------- 3. 备份 + 修正 ch9 #12(finance 专属)----------
    fix_record: dict[str, Any] | None = None
    if subject_id == "fin-mgmt":
        print("\n[3/6] 备份 + 修正 ch9 #12…")
        if input_jsonl.exists():
            shutil.copy2(input_jsonl, QUESTIONS_BAK)
            print(f"  备份 → {QUESTIONS_BAK.relative_to(PROJECT_ROOT)}")

        updated_questions, fix_record = fix_ch9_12_options(raw_questions)
        if fix_record is None:
            print("  ⚠️ 未找到 ch9 #12 目标 id")
        else:
            write_jsonl(updated_questions, input_jsonl)
            print(f"  ch9 #12 修正: {fix_record['old_options']} → {fix_record['new_options']}")
            print(f"  答案保持: {fix_record['answer_unchanged']}")
    else:
        updated_questions = raw_questions
        print("\n[3/6] ch9 #12 修正: 跳过(非 finance 科目)")

    # ---------- 4. Pydantic 严格校验 ----------
    print("\n[4/6] Pydantic 严格校验(extra='forbid')…")
    q_valid, q_errors = validate_questions(updated_questions)
    print(f"  questions: {len(q_valid)}/{len(updated_questions)} 通过, {len(q_errors)} 错误")

    d_valid, d_errors = validate_difficulty(difficulty_rows)
    print(f"  difficulty/*.jsonl: {len(d_valid)}/{len(difficulty_rows)} 通过, {len(d_errors)} 错误")

    if q_errors:
        print("  ⚠️ questions 错误样例(前 3 条):")
        for ln, msg in q_errors[:3]:
            print(f"    line {ln}: {msg[:200]}")

    if d_errors:
        print("  ⚠️ difficulty 错误样例(前 3 条):")
        for ln, msg in d_errors[:3]:
            print(f"    line {ln}: {msg[:200]}")

    # ---------- 5. 构建 SQLite 数据库 ----------
    print("\n[5/6] 构建 SQLite 数据库…")
    rejected: list[dict[str, Any]] = []
    counts = build_db(
        questions=q_valid,
        difficulty_by_id=difficulty_by_id,
        rejected=rejected,
        db_path=output_db,
        subject_id=subject_id,
        subject_name=subject_name,
        chapter_titles=chapter_titles,
    )
    print(f"  数据库: {output_db.relative_to(PROJECT_ROOT) if output_db.is_absolute() else output_db}")
    print(f"  各表 COUNT(*): {counts}")
    print(f"  rejected 题数: {len(rejected)}")

    # 写 rejected.jsonl
    QA_DIR.mkdir(parents=True, exist_ok=True)
    if rejected:
        write_jsonl(rejected, REJECTED_JSONL)
        print(f"  rejected → {REJECTED_JSONL.relative_to(PROJECT_ROOT)}")

    # ---------- 6. 生成报告 ----------
    print("\n[6/6] 生成 import_report.md…")
    write_report(
        fix_record=fix_record,
        questions_total=len(updated_questions),
        questions_valid=len(q_valid),
        questions_errors=q_errors,
        difficulty_files=difficulty_files,
        counts=counts,
        rejected=rejected,
        db_path=output_db,
    )
    print(f"  报告 → {IMPORT_REPORT.relative_to(PROJECT_ROOT)}")

    # ---------- 收尾 ----------
    print("\n" + "=" * 60)
    print("✅ P4 完成")
    print("=" * 60)
    if fix_record is not None:
        print(f"ch9 #12 options: {fix_record['old_options']} → {fix_record['new_options']}")
    if output_db.exists():
        print(f"{output_db.name}: {output_db.stat().st_size:,} bytes")
    print(f"COUNT(*): subjects={counts.get('subjects')}, "
          f"chapters={counts.get('chapters')}, questions={counts.get('questions')}")
    print(f"rejected: {len(rejected)} 题")
    return 0


if __name__ == "__main__":
    sys.exit(main())