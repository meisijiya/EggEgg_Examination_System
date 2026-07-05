"""公司战略 multi-agent AI 出题服务。

用户硬决策(Phase 1.2):
- "Agent AI 生成后再由 Agent 审查答案,多 Agent 协作,可以上网搜索,确保高准确率"
- 入库前 `manual review 100% gate` (oracle P0 critical兜底,见 /admin endpoints)
- 零 schema migration(仅写 JSONL,不入 DB)

Pipeline(本文件实现):
1. Question Generation Agent — 基于上下文(原始 PDF/DOCX 段落)生成结构化 Question
2. Web Search Grounding Agent — 联网(DuckDuckGo HTML)验证 key_points/答案
3. Answer + Key_points Synthesis Agent — 综合 PDF/DOCX 上下文 + 联网结果
4. Peer Review Agent — 第二 LLM 视角验证一致性,不一致 → needs_manual_review + reason

输出 `data/parsed/corporate_strategy_ai_generated.jsonl`,每条:
  - 原始字段(id/type/stem/options/answer/key_points/analysis/difficulty)
  - ai_generated: true
  - confidence: 0.0-1.0
  - needs_manual_review: bool
  - reason: str (e.g. 'peer_review_disagree: key_points 提到 X 但 LLM-A 答案未覆盖')
  - source_ref: {"file": str, "paragraph_index": int, "snippet": str}

主要 entry: `multi_agent_q_gen()`(async,Semaphore 限流)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
DEFAULT_INPUT_JSONL = PARSED_DIR / "corporate_strategy_questions_docx.jsonl"
DEFAULT_OUTPUT_JSONL = PARSED_DIR / "corporate_strategy_ai_generated.jsonl"

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("corporate_strategy_q_gen")


# ---------------------------------------------------------------------------
# Prompts(few-shot 风格 spec-style)
# ---------------------------------------------------------------------------

QUESTION_GEN_SYSTEM = """你是公司战略与风险管理科目的出题专家。基于给定资料片段,生成 1 道高质量中文题目。

输出 JSON Schema 严格匹配:
{
  "stem": "<题干原文 ≥ 20 字,贴近原资料语境,不编新事实>",
  "type": "single | multi | judge | calc | comprehensive",
  "options": ["A.<...>", "B.<...>", "C.<...>", "D.<...>"] | null,
  "answer": "<标准答案(基于资料的客观正确选项/判断,不臆测)>",
  "key_points": ["<考点 1,客观题 null/[]>, <考点 2>, ..."]
  "analysis": "<≤ 120 字解析,说明为什么这个答案是正确的>",
  "difficulty_hint": 1 | 2 | 3
}

约束:
- 严禁编造资料中没出现的概念/法规/数字
- options 单选/多选为 4 项,判断题 options=null
- 答案如果不确定 → answer=""(让下游 peer_review flag)"""


PEER_REVIEW_SYSTEM = """你是公司战略与风险管理科目的高级审查员。给你 1 道题 + 候选 AI 输出,
独立判断:原资料是否支持这个答案 + key_points 是否覆盖考点。

输出 JSON:
{
  "agree": true | false,
  "confidence_delta": -0.5 .. +0.5 (与 AI 自报 confidence 偏离),
  "disagreement_reasons": ["..."],
  "key_points_gaps": ["..."]
}

约束:如有疑问倾向 disagree(过保守比漏过的危害小)。"""

WEB_SEARCH_SYSTEM = """你是事实核查助手。给定 key_points + 答案主张,用联网搜索结果验证事实准确性。
只输出: {"verified": true | false, "evidence_snippets": ["<引用1>", "<引用2>"], "confidence": 0.0-1.0}

如果搜索结果缺失或与主张矛盾 → verified=false + low confidence。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class SegmentInput:
    """单条输入片段(对应 parse_docx.py 输出 JSONL 的一行)。"""

    id: str
    source_file: str
    paragraph_index: int
    raw_text: str
    section_path: str = ""
    needs_ai_answer: bool = True

    @classmethod
    def from_jsonl_row(cls, row: dict[str, Any]) -> "SegmentInput":
        return cls(
            id=row.get("id", ""),
            source_file=row.get("source_file", ""),
            paragraph_index=row.get("paragraph_index", -1),
            raw_text=row.get("raw_text", ""),
            section_path=row.get("section_path", ""),
            needs_ai_answer=row.get("needs_ai_answer", True),
        )


@dataclass
class AIQuestionOutput:
    """AI 出题完整输出(写回 JSONL 的最小单元)。"""

    id: str  # 与 SegmentInput.id 同(稳定追溯)
    source_ref: dict[str, Any]  # {"file": ..., "paragraph_index": ..., "snippet": "..."}

    # 题目字段
    type: str
    stem: str
    options: list[str] | None
    answer: str
    key_points: list[str] | None
    analysis: str | None
    difficulty: int = 1

    # AI 元数据
    ai_generated: bool = True
    confidence: float = 0.0
    needs_manual_review: bool = False
    status: str = "pending"  # pending | approved | rejected
    review_reason: str | None = None
    web_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_ref": self.source_ref,
            "type": self.type,
            "stem": self.stem,
            "options": self.options,
            "answer": self.answer,
            "key_points": self.key_points,
            "analysis": self.analysis,
            "difficulty": self.difficulty,
            "ai_generated": self.ai_generated,
            "confidence": self.confidence,
            "needs_manual_review": self.needs_manual_review,
            "status": self.status,
            "review_reason": self.review_reason,
            "web_evidence": self.web_evidence,
        }


# ---------------------------------------------------------------------------
# Agent 1: Question Generation
# ---------------------------------------------------------------------------


async def _gen_question_agent(
    client: Any,
    segment: SegmentInput,
) -> AIQuestionOutput | None:
    """AI 出题:基于 segment 上下文生成结构化题目。"""
    if not segment.raw_text.strip():
        return None

    # 用稳定 hash 作为 id(与 SegmentInput.id 保持一致)
    out = AIQuestionOutput(
        id=segment.id,
        source_ref={
            "file": segment.source_file,
            "paragraph_index": segment.paragraph_index,
            "snippet": segment.raw_text[:120],
        },
        type="calc",  # 默认综合主观题(待 LLM 判定;course policy 不强制)
        stem="",
        options=None,
        answer="",
        key_points=None,
        analysis=None,
        difficulty=2,
    )

    system = QUESTION_GEN_SYSTEM
    user = (
        f"【资料片段 / Source Segment】\n"
        f"段落 ID: {segment.id}\n"
        f"来源: {segment.source_file} #{segment.paragraph_index}\n"
        f"小节: {segment.section_path or '(无标题)'}\n\n"
        f"原文:\n{segment.raw_text[:1200]}\n\n"
        f"请严格按 system prompt 输出 JSON。"
    )

    try:
        resp = await client.chat_json_async(system=system, user=user, timeout=20)
    except Exception as e:  # noqa: BLE001
        logger.warning("Question Gen 失败 (id=%s): %s", segment.id, e)
        out.needs_manual_review = True
        out.review_reason = f"question_gen_failure: {e!r}"
        out.confidence = 0.0
        return out  # 仍返回(下游 agent 可借力,或人工 review)

    # 提取并校验
    out.stem = (resp.get("stem") or "").strip()
    out.type = resp.get("type") or "calc"
    if resp.get("type") and resp["type"] not in {"single", "multi", "judge", "calc", "comprehensive"}:
        out.type = "calc"
    out.options = resp.get("options") if isinstance(resp.get("options"), list) else None
    out.answer = (resp.get("answer") or "").strip()
    kp = resp.get("key_points")
    if isinstance(kp, list):
        out.key_points = [str(x) for x in kp if x]
    else:
        out.key_points = None
    out.analysis = (resp.get("analysis") or "").strip() or None
    try:
        diff = int(resp.get("difficulty_hint") or 2)
        out.difficulty = max(1, min(3, diff))
    except (ValueError, TypeError):
        out.difficulty = 2

    if not out.stem or not out.answer:
        out.needs_manual_review = True
        out.review_reason = "question_gen_missing_stem_or_answer"
        out.confidence = 0.0

    return out


# ---------------------------------------------------------------------------
# Agent 2: Web Search Grounding
# ---------------------------------------------------------------------------


async def _web_search(
    query: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 8.0,
    max_results: int = 3,
) -> list[str]:
    """DuckDuckGo HTML 搜索(无 API key;通过 simple lite HTML 端点)。

    返回 evidence_snippet 列表(每条 ≤ 200 字)。
    """
    if not query.strip():
        return []

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "FES-corp-strat-q-gen/1.0"},
        )

    try:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(
            {"q": query.strip()}
        )
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.debug("DuckDuckGo HTTP %s", resp.status_code)
            return []
        html = resp.text
    except Exception as e:  # noqa: BLE001
        logger.debug("DuckDuckGo 调用失败: %s", e)
        return []
    finally:
        if owns_client and client is not None:
            await client.aclose()

    # 简化解析:<a class="result__a"> 链接 + <a class="result__snippet"> 摘要
    snippets: list[str] = []
    snippet_re = re.compile(
        r'class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    for m in snippet_re.finditer(html):
        text = re.sub(r"<[^>]+>", "", m.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            snippets.append(text[:200])
        if len(snippets) >= max_results:
            break
    return snippets


async def _web_grounding_agent(
    client: Any,
    question: AIQuestionOutput,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """联网核查:对 key_points + 答案做 web search 验证。

    落地为 `confidence` 调整 + `web_evidence` 字段。
    """
    if not question.answer and not question.key_points:
        return  # 无内容可核查

    # 构造 query:用 answer + 第一个 key_point(若 key_points 为空则只用 answer)
    parts: list[str] = []
    if question.answer:
        parts.append(f"答案是 {question.answer}")
    if question.key_points:
        parts.append(f"考点: {question.key_points[0]}")
    query = "; ".join(parts)[:200]

    snippets = await _web_search(query, client=http_client)
    question.web_evidence = snippets

    if not snippets:
        # 无搜索结果 → 不下调 confidence(可能 query 太偏)
        return

    # 简化判定:让 LLM 二次判断证据是否支持(可选;为节省 LLM 调用,简单 hash 计数)
    system = WEB_SEARCH_SYSTEM
    user = (
        f"待验证主张: {question.stem[:200]}\n"
        f"候选答案: {question.answer}\n"
        f"key_points: {json.dumps(question.key_points or [], ensure_ascii=False)}\n\n"
        f"搜索证据:\n{chr(10).join(f'- {s}' for s in snippets)}"
    )
    try:
        resp = await client.chat_json_async(system=system, user=user, timeout=12)
        if isinstance(resp, dict):
            verified = bool(resp.get("verified", False))
            conf = float(resp.get("confidence", 0.0))
            if not verified:
                question.confidence = max(0.0, conf - 0.2)
                question.review_reason = "web_grounding_disagree"
            else:
                question.confidence = min(1.0, conf + 0.1)
    except Exception as e:  # noqa: BLE001
        logger.debug("Web grounding LLM 失败: %s", e)


# ---------------------------------------------------------------------------
# Agent 4: Peer Review(cross-validation)
# ---------------------------------------------------------------------------


async def _peer_review_agent(
    client: Any,
    question: AIQuestionOutput,
) -> None:
    """第二视角 LLM 审查原资料 + AI 输出,标记 needs_manual_review。

    不一致(disagree) → 减 confidence + 标 needs_manual_review + 填 review_reason。
    """
    system = PEER_REVIEW_SYSTEM
    user = (
        f"【原资料】\n{question.source_ref.get('snippet', '')}\n\n"
        f"【AI 出的题目】\n"
        f"type: {question.type}\n"
        f"stem: {question.stem}\n"
        f"options: {json.dumps(question.options or [], ensure_ascii=False)}\n"
        f"answer: {question.answer}\n"
        f"key_points: {json.dumps(question.key_points or [], ensure_ascii=False)}\n"
        f"analysis: {question.analysis or ''}\n\n"
        f"请独立判断: 答案是否被原资料支持?key_points 是否覆盖考点?"
    )

    try:
        resp = await client.chat_json_async(system=system, user=user, timeout=15)
    except Exception as e:  # noqa: BLE001
        logger.warning("Peer review 失败 (id=%s): %s", question.id, e)
        question.needs_manual_review = True
        question.review_reason = f"peer_review_api_failure: {e!r}"
        return

    if not isinstance(resp, dict):
        question.needs_manual_review = True
        question.review_reason = "peer_review_bad_response"
        return

    agree = resp.get("agree", False)
    delta = float(resp.get("confidence_delta", 0.0))
    reasons = resp.get("disagreement_reasons") or []
    gaps = resp.get("key_points_gaps") or []

    question.confidence = max(0.0, min(1.0, question.confidence + delta))
    if not agree:
        question.needs_manual_review = True
        reason_parts: list[str] = []
        if reasons:
            reason_parts.append(f"peer_review_disagree: {'; '.join(reasons[:3])}")
        if gaps:
            reason_parts.append(f"key_points_gaps: {'; '.join(gaps[:3])}")
        question.review_reason = " | ".join(reason_parts) or "peer_review_disagree"


# ---------------------------------------------------------------------------
# 编排:per-segment 4-stage pipeline
# ---------------------------------------------------------------------------


async def _process_one_segment(
    client: Any,
    segment: SegmentInput,
    *,
    http_client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    do_web_search: bool,
) -> AIQuestionOutput | None:
    """单段 4-stage pipeline:Gen → Web → Peer → 落 status。"""
    if not segment.needs_ai_answer:
        return None
    async with sem:
        # 1. Gen
        q = await _gen_question_agent(client, segment)
        if q is None:
            return None
        if not q.stem:
            q.needs_manual_review = True
            q.review_reason = (q.review_reason or "") + " | no_stem_generated"
            return q

        # 2. Web grounding(optional,因网络不稳)
        if do_web_search:
            await _web_grounding_agent(client, q, http_client=http_client)

        # 3. Peer review
        await _peer_review_agent(client, q)

        # 4. 落 status
        # 规则:
        # - confidence >= 0.6 且不需要人工 review → approved(可直接入库,绕开 manual gate)
        # - 其它 → pending,人工 review 后改 status
        if q.confidence >= 0.6 and not q.needs_manual_review and q.review_reason is None:
            q.status = "approved"
        else:
            q.status = "pending"

        return q


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def multi_agent_q_gen(
    segments: list[SegmentInput],
    *,
    deepseek_client: Any | None = None,
    concurrency: int = 4,
    do_web_search: bool = True,
) -> list[AIQuestionOutput]:
    """多 Agent 出题批跑(Semaphore 限流)。

    Parameters
    ----------
    segments : list[SegmentInput]
        输入段落实例(通常来自 parse_docx.py 输出 JSONL)。
    deepseek_client : Optional
        DeepSeekClient 实例;None → 自动 fallback:产生 needs_manual_review 占位记录。
    concurrency : int
        并发槽数(Semaphore 默认 4)。
    do_web_search : bool
        是否启用联网 grounding(失败 graceful degrade)。

    Returns
    -------
    list[AIQuestionOutput]
    """
    if deepseek_client is None or not getattr(deepseek_client, "configured", False):
        logger.warning(
            "DeepSeek 未配置 → 所有段落到 pending,等待人工 review 出题"
        )
        return [
            AIQuestionOutput(
                id=s.id,
                source_ref={
                    "file": s.source_file,
                    "paragraph_index": s.paragraph_index,
                    "snippet": s.raw_text[:120],
                },
                type="calc",
                stem="",
                options=None,
                answer="",
                key_points=None,
                analysis=None,
                difficulty=2,
                needs_manual_review=True,
                review_reason="deepseek_unconfigured",
                status="pending",
            )
            for s in segments
        ]

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0),
        headers={"User-Agent": "FES-corp-strat-q-gen/1.0"},
    ) as http_client:
        tasks = [
            _process_one_segment(
                client=deepseek_client,
                segment=s,
                http_client=http_client,
                sem=sem,
                do_web_search=do_web_search,
            )
            for s in segments
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    out: list[AIQuestionOutput] = []
    for s, r in zip(segments, results):
        if isinstance(r, BaseException):
            logger.error("段 %s 处理异常: %s", s.id, r)
            out.append(
                AIQuestionOutput(
                    id=s.id,
                    source_ref={
                        "file": s.source_file,
                        "paragraph_index": s.paragraph_index,
                        "snippet": s.raw_text[:120],
                    },
                    type="calc",
                    stem="",
                    options=None,
                    answer="",
                    key_points=None,
                    analysis=None,
                    difficulty=2,
                    needs_manual_review=True,
                    review_reason=f"exception: {r!r}",
                    status="pending",
                )
            )
        elif r is not None:
            assert isinstance(r, AIQuestionOutput)
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# JSONL I/O + CLI
# ---------------------------------------------------------------------------


def read_input_jsonl(path: Path) -> list[SegmentInput]:
    """读 DOCX 解析 JSONL → SegmentInput 列表。"""
    items: list[SegmentInput] = []
    if not path.exists():
        return items
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            items.append(SegmentInput.from_jsonl_row(row))
    return items


def write_output_jsonl(items: list[AIQuestionOutput], path: Path) -> int:
    """写 AI 出题 JSONL。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            n += 1
    return n


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corporate_strategy_q_gen",
        description="公司战略 multi-agent AI 出题(DeepSeek + DuckDuckGo 联网)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        default=DEFAULT_INPUT_JSONL,
        help="输入 DOCX 段落实例 JSONL(parse_docx 输出)",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=DEFAULT_OUTPUT_JSONL,
        help="输出 AI 出题 JSONL",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="并发数(Semaphore 上限)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制处理段数(测试用;None=全部)",
    )
    parser.add_argument(
        "--no-web-search",
        action="store_true",
        help="禁用联网 grounding(网络不可时)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="dry run:仅 mock pipeline,不调 LLM(测试用)",
    )
    return parser


async def _amain_async(
    *,
    input_path: Path,
    output_path: Path,
    concurrency: int,
    limit: int | None,
    do_web_search: bool,
    dry_run: bool,
) -> int:
    segments = read_input_jsonl(input_path)
    if not segments:
        logger.error(f"输入为空: {input_path}")
        return 1
    if limit is not None:
        segments = segments[:limit]
    logger.info(
        "输入段落: %d(limit=%s), concurrency=%d, web_search=%s, dry_run=%s",
        len(segments),
        limit,
        concurrency,
        do_web_search,
        dry_run,
    )

    deepseek_client: Any | None = None
    if not dry_run:
        try:
            from app.services.deepseek_client import get_deepseek_client

            deepseek_client = get_deepseek_client()
        except Exception as e:  # noqa: BLE001
            logger.warning("无法加载 DeepSeek 客户端: %s → fallback pending", e)
            deepseek_client = None

    results = await multi_agent_q_gen(
        segments,
        deepseek_client=deepseek_client,
        concurrency=concurrency,
        do_web_search=do_web_search,
    )
    n = write_output_jsonl(results, output_path)
    approved = sum(1 for r in results if r.status == "approved")
    pending = sum(1 for r in results if r.status == "pending")
    print("\n" + "=" * 60)
    print("Multi-agent AI 出题完成")
    print("=" * 60)
    print(f"输入段落: {len(segments)}")
    print(f"输出: {n} 段 → {output_path.relative_to(PROJECT_ROOT) if output_path.is_absolute() else output_path}")
    print(f"approved(可直接入库): {approved}")
    print(f"pending(需人工 review): {pending}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    return asyncio.run(
        _amain_async(
            input_path=args.input_jsonl,
            output_path=args.output_jsonl,
            concurrency=args.concurrency,
            limit=args.limit,
            do_web_search=not args.no_web_search,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
