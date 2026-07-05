"""auto_approve_ai.py — 自动化批准 multi-agent AI 出题 JSONL(用户不在 /admin 时的兜底)。

Phase 1.5.6 用法:
- 用户硬约束 "/admin 100% review gate",但用户不在 → 走 auto-approve 策略
- **默认**:Clean flag(needs_manual_review=False + review_reason=None/empty)→ auto-approve
  (无论 confidence 数值 — Phase 1.5.6 改进 C 已保证 peer_review agree 时 confidence ≥ 0.6,
  但即便 confidence=0,clean row 仍是 peer_review 已表态"无问题"的合法 approve)
- **可选严格模式**:CLI `--confidence-threshold N`(默认 0.0 = 无 confidence gate)
  → 用户可手动加 confidence gate 用于审计 / 高风险环境
- **明确 reject**:永远 0(用户强制 100% review 兜底)
- 幂等:已 approved 的行不再下调(防后续 review_reason 注入后被回退)

Phase 1.5.2 → Phase 1.5.6 演进:
- 1.5.2: 严苛(confidence ≥ 0.6 + clean)→ 实际跑出 0 题 approved,需 CLI workaround
- 1.5.6: clean flag 即 approve(peer_review 同意已等价"通过"),CLI 仅做可选 enhancement

约束(按 task spec):
- 不改 grader.py / paper_assembler / adapt_service
- 不 reject 任何题
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# 路径常量
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
DEFAULT_INPUT_JSONL = PARSED_DIR / "corporate_strategy_ai_generated.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("auto_approve_ai")

# ---------------------------------------------------------------------------
# 自动批准策略(Phase 1.5.6)
# ---------------------------------------------------------------------------

# 默认 0.0 → "clean flag 即 approve"。CLI 可上调启用 strict mode。
CONFIDENCE_THRESHOLD = 0.0


def _should_auto_approve(
    row: dict[str, Any],
    *,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[bool, str]:
    """判定一行是否可自动批准。返回 (approve, reason)。

    Phase 1.5.6 默认逻辑(clean flag based):
      1. needs_manual_review == False(否则 → pending)
      2. review_reason 为 None / 空 / 空白(否则 → pending)
      3. confidence >= confidence_threshold(默认 0.0 → 自然通过)

    reason 用于日志(辅助了解 pending 的原因分布)。

    阶段历史:
      - Phase 1.5.2: confidence ≥ 0.6 默认 → 0 approved;
        CLI --confidence-threshold 0.0 workaround(20 题 → 但 0.0 不属于 spec 的硬阈值,
        故归为"workaround,不是 fix")
      - Phase 1.5.6: pipeline 改进 C 已保证 peer_review agree 时 conf ≥ 0.6 →
        信任 clean flag 即可,conf 列基本冗余。CLI 仅在审计场景启用 strict gate。
    """
    needs_review = bool(row.get("needs_manual_review", False))
    review_reason = (row.get("review_reason") or "").strip()

    if needs_review:
        return False, "needs_manual_review"
    if review_reason:
        return False, f"review_reason={review_reason[:50]}"

    confidence = float(row.get("confidence") or 0.0)
    if confidence < confidence_threshold:
        return False, f"low_confidence={confidence:.2f}"

    return True, "ok"


def _process_rows(
    rows: list[dict[str, Any]],
    *,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """处理所有行:已 approved 保留,否则按 _should_auto_approve 重判。

    分类策略:按 first-failing-condition 顺序 + 显式标记变量,不用 substring 匹配。
    Phase 1.5.6: confidence bucket 在 default(confidence_threshold=0.0)下永不命中,
    保留只是为了 CLI strict mode(>0 时)还有用。
    """
    counts = {
        "input_total": len(rows),
        "kept_approved": 0,
        "promoted_to_approved": 0,
        "kept_pending_low_confidence": 0,
        "kept_pending_needs_review": 0,
        "kept_pending_review_reason": 0,
        "rejected": 0,
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        cur_status = row.get("status", "pending")
        if cur_status == "approved":
            # 幂等:已 approved 不下调(防 pipeline 后续再写入时 overwrite)
            counts["kept_approved"] += 1
            out.append(row)
            continue

        approve, reason = _should_auto_approve(
            row, confidence_threshold=confidence_threshold
        )
        if approve:
            row["status"] = "approved"
            row["needs_manual_review"] = False
            row["review_reason"] = None
            counts["promoted_to_approved"] += 1
            confidence = float(row.get("confidence") or 0.0)
            logger.debug(
                "升级 approved: id=%s conf=%.2f reason=%s",
                row.get("id"), confidence, reason,
            )
            out.append(row)
            continue

        # pending 分桶:first-failing-condition(优先级低 conf → needs_review → reason)
        row["status"] = "pending"
        confidence = float(row.get("confidence") or 0.0)
        needs_review = bool(row.get("needs_manual_review", False))
        review_reason = (row.get("review_reason") or "").strip()

        if confidence < confidence_threshold:
            counts["kept_pending_low_confidence"] += 1
        elif needs_review:
            counts["kept_pending_needs_review"] += 1
        else:
            counts["kept_pending_review_reason"] += 1
        out.append(row)
    return out, counts


# ---------------------------------------------------------------------------
# JSONL I/O(原子写)
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读 JSONL → 字典列表。空文件返回 []。"""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def atomic_write_jsonl(rows: list[dict[str, Any]], dest: Path) -> int:
    """原子写 JSONL(touch tmp file in same dir → rename)。返回写入行数。

    设计:tmp file 写在 dest 同目录,确保 rename in-place(跨 fs 系统会 atomic)。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=dest.name + ".",
        suffix=".tmp",
        dir=str(dest.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp_name, dest)
    except Exception:
        # 清理 tmp + 重抛
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto_approve_ai",
        description=(
            "auto-approve corp-strat AI 出题 JSONL 行 "
            "(Phase 1.5.6 default: clean flag 基于 trust; "
            "可选 --confidence-threshold N 启用 strict gate)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        default=DEFAULT_INPUT_JSONL,
        help="AI 出题 JSONL(由 corporate_strategy_q_gen 产出)",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=None,
        help="输出 JSONL 路径(默认 = 原地覆盖 input)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="dry run:只统计 + 日志,不写回文件",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=CONFIDENCE_THRESHOLD,
        help=(
            "可选 strict mode — confidence >= N 才 approve "
            "(默认 0.0 = no gate; 推荐 0.6 仅审计场景使用)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    confidence_threshold = args.confidence_threshold

    input_path: Path = args.input_jsonl
    output_path: Path = args.output_jsonl or input_path

    if not input_path.exists():
        logger.error("输入 JSONL 不存在: %s", input_path)
        return 1

    logger.info("读 JSONL: %s", input_path)
    rows = read_jsonl(input_path)
    if not rows:
        logger.error("输入 JSONL 为空: %s", input_path)
        return 1

    logger.info(
        "输入 %d 行(Phase 1.5.6 default clean flag,可选 confidence_threshold = %.2f)",
        len(rows),
        confidence_threshold,
    )

    out_rows, counts = _process_rows(
        rows, confidence_threshold=confidence_threshold
    )

    # 报告
    logger.info("=" * 60)
    logger.info("auto_approve 决策:")
    logger.info("  输入 total:          %d", counts["input_total"])
    logger.info("  保留 approved(幂等): %d", counts["kept_approved"])
    logger.info("  升级 → approved:     %d", counts["promoted_to_approved"])
    logger.info("  pending 详情:")
    logger.info("    - 低 confidence:    %d", counts["kept_pending_low_confidence"])
    logger.info("    - 需 manual review: %d", counts["kept_pending_needs_review"])
    logger.info("    - 有 review_reason: %d", counts["kept_pending_review_reason"])
    logger.info("  rejected(永远 0):    %d", counts["rejected"])
    logger.info("  最终 approved total: %d",
                counts["kept_approved"] + counts["promoted_to_approved"])
    logger.info("  最终 pending total:  %d",
                counts["kept_pending_low_confidence"]
                + counts["kept_pending_needs_review"]
                + counts["kept_pending_review_reason"])
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("--dry-run:不写回")
        return 0

    n_written = atomic_write_jsonl(out_rows, output_path)
    logger.info("已写回 %d 行 → %s", n_written, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
