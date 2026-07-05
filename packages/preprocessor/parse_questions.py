"""PDF 题目解析器。

将财务管理资料中的 12 个 PDF 文件解析为统一的 JSONL 题目库。

设计原则：
- **零 LLM 边界**：仅使用 pdfplumber + 正则 + 启发式规则
- **结构化提取**：识别章节、题型、单/多/判/填空题
- **严格校验**：每条记录通过 Pydantic 模型校验（extra='forbid'）
- **失败显式**：所有解析失败写入 ``data/parsed/errors.log``，绝不静默吞错
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_DIR_DEFAULT = PROJECT_ROOT / "财务管理资料"
PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
BY_PDF_DIR = PARSED_DIR / "by_pdf"
ERRORS_LOG = PARSED_DIR / "errors.log"

# ---------------------------------------------------------------------------
# 类型枚举（白名单）
# ---------------------------------------------------------------------------

# 财务科目章节 ch1~ch9（兼容科目扩展时，新科目可注入更长章节范围）
CHAPTER_WHITELIST = {f"ch{i}" for i in range(1, 10)}  # ch1 ~ ch9
TYPE_WHITELIST = {"single", "multi", "judge", "calc", "comprehensive"}

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("parse_questions")

_ERRORS_FILE_HANDLE: Any = None


def _log_error(pdf_name: str, page_ref: int, msg: str) -> None:
    """记录解析错误到 errors.log（线程不安全的简单实现）。"""
    global _ERRORS_FILE_HANDLE
    if _ERRORS_FILE_HANDLE is None:
        ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
        _ERRORS_FILE_HANDLE = open(ERRORS_LOG, "a", encoding="utf-8")
    _ERRORS_FILE_HANDLE.write(f"[{pdf_name}] page={page_ref}: {msg}\n")
    _ERRORS_FILE_HANDLE.flush()


# ---------------------------------------------------------------------------
# Pydantic Schema
# ---------------------------------------------------------------------------


class Question(BaseModel):
    """一道题目的标准化记录。

    使用 ``extra='forbid'`` 严格校验：解析时若产生未声明字段会立即报错，
    避免静默写入脏数据。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=8, description="稳定 UUID 或 hash")
    type: str = Field(..., description="single/multi/judge/calc/comprehensive")
    chapter: str = Field(..., description="ch1..ch9")
    number: int = Field(..., ge=1, description="题号（PDF 内编号）")
    stem: str = Field(..., min_length=1, description="题干原文")
    options: list[str] | None = Field(default=None, description="选项数组")
    answer: str = Field(..., min_length=1, description="标准答案")
    key_points: list[str] | None = Field(default=None, description="主观题关键要点")
    analysis: str | None = Field(default=None, description="答案解析")
    difficulty: None = Field(default=None, description="难度（阶段 ②.5 由 DeepSeek 填充）")
    source_pdf: str = Field(..., min_length=1, description="原始 PDF 文件名")
    page_ref: int = Field(..., ge=1, description="题目起始页码")


# ---------------------------------------------------------------------------
# 章节识别
# ---------------------------------------------------------------------------

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def detect_chapter(pdf_basename: str, text: str) -> str:
    """从 PDF 文件名 + PDF 文本推断章节（ch1..ch9）。

    优先级：
    1. 文件名中显式的中文「第N章」/「第N章 副标题」
    2. 文件名中显式的阿拉伯「第N章」
    3. PDF 首页开头的「第N章」标题
    4. 兜底：文件名中的「第N章」
    """
    # 1. 中文「第N章」
    m = re.search(r"第([一二三四五六七八九])章", pdf_basename)
    if m:
        return f"ch{_CN_NUM[m.group(1)]}"

    # 2. 阿拉伯「第N章」
    m = re.search(r"第(\d+)章", pdf_basename)
    if m:
        n = int(m.group(1))
        if n in range(1, 10):
            return f"ch{n}"

    # 3. PDF 文本头部
    head = text[:300]
    m = re.search(r"第([一二三四五六七八九])章", head)
    if m:
        return f"ch{_CN_NUM[m.group(1)]}"
    m = re.search(r"第(\d+)章", head)
    if m:
        n = int(m.group(1))
        if n in range(1, 10):
            return f"ch{n}"

    # 4. 兜底：从文件名抽取数字
    digits = re.findall(r"(\d+)", pdf_basename)
    for d in digits:
        n = int(d)
        if n in range(1, 10):
            return f"ch{n}"

    raise ValueError(f"无法从 PDF 文件名/内容识别章节: {pdf_basename!r}")


# ---------------------------------------------------------------------------
# 题型分组识别
# ---------------------------------------------------------------------------

# 题型分组标记，例如 "一. 单选题（共15题）" 或 "二.多选题（共 16 题）"
_TYPE_GROUP_RE = re.compile(
    r"^\s*([一二三四五六七八九十])\s*[.、]\s*"
    r"(单选题|多选题|判断题|填空题|计算分析题|计算题|综合题)"
    r"(?:\s*[（(]\s*(?:共|约)?\s*\d+\s*题\s*[）)])?",
    re.MULTILINE,
)


def _type_label_to_enum(label: str) -> str:
    """将中文题型标签映射到枚举值。"""
    mapping = {
        "单选题": "single",
        "多选题": "multi",
        "判断题": "judge",
        "填空题": "calc",
        "计算分析题": "calc",
        "计算题": "calc",
        "综合题": "comprehensive",
    }
    if label not in mapping:
        raise ValueError(f"未知题型标签: {label!r}")
    return mapping[label]


def detect_type_groups(text: str) -> list[tuple[str, str]]:
    """检测 PDF 文本中的所有题型分组标记。

    返回 [(type_label, raw_match), ...] 列表，按出现顺序排列。
    """
    results: list[tuple[str, str]] = []
    for m in _TYPE_GROUP_RE.finditer(text):
        label = m.group(2)
        results.append((label, m.group(0).strip()))
    # 去重（第九章 即测即评 有重复 "二、多选题" 与 "二.多选题"）
    seen = set()
    deduped: list[tuple[str, str]] = []
    for label, raw in results:
        key = (label,)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, raw))
    return deduped


# ---------------------------------------------------------------------------
# PDF 文本提取
# ---------------------------------------------------------------------------

def extract_pdf_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """逐页提取 PDF 文本，返回 [(page_no, text), ...]。

    空页（含图表但无可提取文字）用空字符串占位，便于页码追踪。
    """
    pages: list[tuple[int, str]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append((i, text))
    return pages


# ---------------------------------------------------------------------------
# 文本预处理：把跨页题目拼接回来
# ---------------------------------------------------------------------------

# 单题起始锚点：<数字><可选空白/点>【<类型>】  或  <数字>.(<类型>)
# 支持格式:
#   "1\n【单选题】"           标准格式
#   "1.(单选题)题干"          即测即评格式（半角括号 + 点）
#   "1（单选题）题干"          全角括号变体
#   "1\n【 单选题 】"          带空格变体
_QUESTION_START_RE = re.compile(
    r"^\s*(?P<num>\d+)[\s\.]*?"
    r"(?:【\s*(?P<type1>单选题|多选题|判断题|填空题|计算分析题|计算题|综合题)\s*】"
    r"|[（(]\s*(?P<type2>单选题|多选题|判断题|填空题|计算分析题|计算题|综合题)\s*[）)])",
    re.MULTILINE,
)

# 选项行：A、 或 A.
_OPTION_LINE_RE = re.compile(r"^\s*([A-Z])[、.]\s*", re.MULTILINE)

# 答案行
_ANSWER_RE = re.compile(r"(?:正确答案|我的答案|参考答案|标准答案)\s*[：:]\s*(.+?)(?=\n|$)")

# 答案/解析/知识点前缀（用于在选项收集中终止扫描）
# 注意：必须能匹配 "正确答案：" 这种"答案在下一行"的孤立前缀
# 前导字符可以是行首、换行、或任意标点/中文标点；用于题干清洗时容忍行内拼接
_OPTION_BREAK_PREFIX_RE = re.compile(
    r"(?:^|[\n。：；，、])\s*(?:正确答案|我的答案|参考答案|标准答案|答案解析|知识点|分\s*析|知\s*识\s*点)\s*[：:]?"
)

# 解析/知识点行
_ANALYSIS_RE = re.compile(r"答案解析\s*[：:]\s*")
_KNOWLEDGE_RE = re.compile(r"^知识点\s*[：:]?\s*$", re.MULTILINE)


def _normalize_text(pages: list[tuple[int, str]]) -> tuple[str, list[int]]:
    """把所有页文本拼接成单一字符串，并用 ``\\n[PAGE=n]\\n`` 标记每页起点。

    返回 (joined_text, line_to_page)，后者把每行映射到其所在页码，
    用于回填 ``page_ref``。
    """
    chunks: list[str] = []
    line_to_page: list[int] = []
    for page_no, text in pages:
        if not text:
            continue
        # 用 \n 切分，保留原始换行结构
        for line in text.split("\n"):
            chunks.append(line)
            line_to_page.append(page_no)
        chunks.append(f"[PAGE={page_no}]")
        line_to_page.append(page_no)
    joined = "\n".join(chunks)
    # 关键修复：**清理跨页题目在题号与题型标记之间的 [PAGE=N]**
    # 例如: "12\n[PAGE=8]\n【单选题】" → "12\n【单选题】"
    joined = re.sub(
        r"(\n\d+[\s\.]*?)\n\[PAGE=\d+\]\n(?=[【（(])",
        r"\1\n",
        joined,
    )
    return joined, line_to_page


# 跨页标记正则（用于清洗选项/答案中的 PAGE 残留）
_PAGE_MARK_RE = re.compile(r"\[PAGE=\d+\]")


def _slice_page_ref(line_to_page: list[int], start_line: int, text_len: int) -> int:
    """根据行级页码映射返回题目的起始页码。"""
    if start_line < 0 or start_line >= len(line_to_page):
        return 1
    return line_to_page[start_line]


# ---------------------------------------------------------------------------
# 单题解析
# ---------------------------------------------------------------------------

def _split_questions(text: str) -> list[tuple[int, int, str, str]]:
    """把全文按题号 + 【类型】 切分成单题片段。

    返回列表 [(num, line_start, type_label, body), ...]。
    """
    out: list[tuple[int, int, str, str]] = []
    matches = list(_QUESTION_START_RE.finditer(text))
    for i, m in enumerate(matches):
        num = int(m.group("num"))
        type_label = m.group("type1") or m.group("type2")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        line_start = text[: m.start()].count("\n") + 1
        out.append((num, line_start, type_label, body))
    return out


def _extract_options(body: str) -> tuple[list[str] | None, str]:
    """从题干 + 选项 + 答案的混合文本中提取选项数组。

    关键不变量：**一旦选项收集因"正确答案/我的答案/答案解析/知识点"终止，就不再重启**，
    否则会误把答案解析里的"A、正确"这种句子当成新选项。

    返回 (options, body_without_options)。如果未识别到选项，返回 (None, body)。
    """
    lines = body.split("\n")
    pos = 0
    in_options = False  # 是否在选项收集中
    options_locked = False  # 一旦遇到 break 前缀，永久锁定（防止重启）
    cur_opt: list[str] = []
    cur_label = ""
    cur_start = -1
    opt_blocks: list[tuple[int, int, str]] = []

    for line in lines:
        if not options_locked:
            m = _OPTION_LINE_RE.match(line)
            if m and not in_options:
                # 开始新选项
                cur_label = m.group(1)
                cur_opt = [line[m.end():].strip()]
                cur_start = pos
                in_options = True
            elif m and in_options:
                # 同一选项区域内，开始下一个选项
                opt_blocks.append((cur_start, pos, f"{cur_label}、{' '.join(cur_opt).strip()}"))
                cur_label = m.group(1)
                cur_opt = [line[m.end():].strip()]
                cur_start = pos
            elif in_options:
                stripped = line.strip()
                # 空行/解析/答案/知识点 终止选项收集，且永久锁定
                if not stripped or _OPTION_BREAK_PREFIX_RE.match(stripped):
                    opt_blocks.append((cur_start, pos, f"{cur_label}、{' '.join(cur_opt).strip()}"))
                    cur_label = ""
                    cur_opt = []
                    in_options = False
                    options_locked = True  # 不再重启
                else:
                    cur_opt.append(stripped)
        pos += len(line) + 1  # +1 for \n

    if cur_label and not options_locked:
        opt_blocks.append((cur_start, pos, f"{cur_label}、{' '.join(cur_opt).strip()}"))

    if not opt_blocks:
        return None, body

    # 清洗选项内容中的 [PAGE=N] 跨页标记残留
    cleaned_opts: list[str] = []
    for opt in opt_blocks:
        text = _PAGE_MARK_RE.sub("", opt[2]).strip()
        text = re.sub(r"\s+", " ", text)  # 多余空白合并
        cleaned_opts.append(text)

    # 提取题干（选项之前）
    first_opt_start = opt_blocks[0][0]
    stem = body[:first_opt_start].strip()
    # 移除开头的"题型"标记残留（极少数 PDF 重复出现）
    stem = re.sub(r"^[（(]?(单选题|多选题|判断题|填空题|计算分析题|综合题)[）)]?\s*[（(]?[\d.]*\s*[）)]?", "", stem).strip()

    options = [o for o in cleaned_opts if o]

    # 重建剩余 body（不含选项）
    last_opt_end = opt_blocks[-1][1]
    rest = body[last_opt_end:]

    return options, stem + "\n" + rest


def _extract_answer(rest: str, type_enum: str) -> tuple[str, str | None, str | None]:
    """从剩余文本中提取 (answer, analysis, knowledge_points_block)。

    处理多种答案位置：
    - 同行：``正确答案：X``
    - 跨行：``正确答案：\\nX``
    - 即测即评：``正确答案:A:文字;`` → 提取 ``A``
    - 填空题：``正确答案：\\n第一空：\\n77.22\\n第二空：\\n125.78`` → 保留全文
    - 裸判断：开头就是 ``对/错``

    answer 必有值；analysis 与 knowledge 可能为 None。
    """
    answer = ""
    analysis: str | None = None
    knowledge_block: str | None = None

    # 优先级：正确答案 > 我的答案 > 参考答案 > 裸对/错
    # 即使"我的答案"出现更早，也优先用"正确答案"，避免误吞后续标记
    m_correct = re.search(r"正确答案\s*[：:]", rest)
    m_my = re.search(r"我的答案\s*[：:]", rest)
    m_ref = re.search(r"(?:参考答案|标准答案)\s*[：:]", rest)

    # 优先级排序：正确答案(0) > 参考答案(1) > 我的答案(2)
    priority_order = {"m_correct": 0, "m_ref": 1, "m_my": 2}
    chosen = None
    if m_correct is not None:
        chosen = ("m_correct", m_correct)
    if chosen is None and m_ref is not None:
        chosen = ("m_ref", m_ref)
    if chosen is None and m_my is not None:
        chosen = ("m_my", m_my)

    if chosen is not None:
        _, m = chosen
        after_marker = rest[m.end():]
        # 填空题（calc）需要保留多空答案
        if type_enum == "calc":
            # 取到 "答案解析" / "知识点" / EOF 之前的全部内容
            cut = re.search(r"\n\s*(?:答案解析|知识点)\s*[：:]", after_marker)
            if cut:
                answer = after_marker[: cut.start()].strip()
            else:
                answer = after_marker.strip()
        else:
            # 客观题：取标记后的第一个非空连续行
            stripped = after_marker.lstrip("\n")
            lines = stripped.split("\n")
            ans_lines: list[str] = []
            for line in lines:
                s = line.strip()
                # 遇到任意答案/解析/知识点前缀就停
                if _OPTION_BREAK_PREFIX_RE.match(s):
                    break
                if s == "" and ans_lines:
                    break
                ans_lines.append(s)
            answer = " ".join(ans_lines).strip()

            # 即测即评格式 "A:文字" → 剥离到字母为止
            m_prefix = re.match(r"^\s*([A-E]+)\s*[：:]", answer)
            if m_prefix:
                answer = m_prefix.group(1).strip()
    else:
        # 裸判断：开头就是 "对/错"
        bare = re.match(r"^\s*(对|错)\s*$", rest.strip())
        if bare:
            answer = bare.group(1).strip()

    # 答案解析
    m_an = _ANALYSIS_RE.search(rest)
    if m_an:
        an_start = m_an.end()
        an_text = rest[an_start:]
        # 清洗 PAGE 标记
        an_text = _PAGE_MARK_RE.sub("", an_text)
        m_know = re.search(r"\n\s*知识点\s*[：:]", an_text)
        if m_know:
            analysis = an_text[: m_know.start()].strip()
            knowledge_block = an_text[m_know.end():].strip()
        else:
            analysis = an_text.strip()

    # 答案也清洗
    answer = _PAGE_MARK_RE.sub("", answer).strip()

    return answer, analysis, knowledge_block


def _clean_answer_for_objective(type_enum: str, answer: str, options: list[str] | None) -> str:
    """客观题答案规范化：单选 → 'A'；多选 → 'ABC'；判断 → '对'/'错'。"""
    if type_enum == "single":
        m = re.match(r"\s*([A-D])\b", answer)
        return m.group(1) if m else answer.strip()
    if type_enum == "multi":
        # 提取首段连续的 A-E 字母（避免 [PAGE=17] 中的 E 误匹配）
        m = re.match(r"^\s*([A-E]+)", answer)
        return "".join(sorted(set(m.group(1)))) if m else answer.strip()
    if type_enum == "judge":
        if "对" in answer or "正确" in answer:
            return "对"
        if "错" in answer or "不正确" in answer or "错误" in answer:
            return "错"
        return answer.strip()
    return answer.strip()


def _ensure_options_for_judge(type_enum: str, options: list[str] | None) -> list[str] | None:
    """判断题自动补 ['对', '错']；客观题缺选项返回 None；其他题型返回原值。"""
    if type_enum == "judge":
        return ["对", "错"]
    return options


def _extract_key_points(knowledge_block: str | None, analysis: str | None,
                        answer: str | None = None,
                        stem: str | None = None) -> list[str]:
    """从"知识点"块/解析/答案/题干中启发式提取关键要点。

    启发式优先级（每个来源独立去重添加，达到 3 个就停）：
    1. 知识点块按行/编号拆（如 "2.1.1 货币时间价值的概述"）→ 直接作为要点
    2. 填空题答案（"第一空：X\n第二空：Y"）→ 拆出每空作为要点
    3. 解析中按句号/分号切短句（6-40 字）
    4. 财务术语提取（从题干+解析+答案中匹配"X资本成本/X利率/X收益率"等术语）
    """
    points: list[str] = []

    # 1. 知识点块
    if knowledge_block:
        cleaned = knowledge_block.replace("\u3000", " ").strip()
        raw_lines = [s.strip() for s in re.split(r"\n|；|;|（[^）]*）|（", cleaned) if s.strip()]
        for line in raw_lines:
            line = re.sub(r"^\d+(?:\.\d+){0,3}\s*", "", line).strip()
            if line and len(line) <= 60 and line not in points:
                points.append(line)

    # 2. 填空题答案
    if answer and "第一空" in answer:
        for m in re.finditer(r"第[一二三四五六七八九十]+空[：:]\s*([^\n]+)", answer):
            content = m.group(1).strip()
            if content and content not in points and len(content) <= 50:
                points.append(content)
                if len(points) >= 3:
                    return points

    # 3. 解析短句
    if analysis and len(points) < 4:
        sentences = re.split(r"[。！？\n;；]+", analysis)
        for s in sentences:
            s = s.strip()
            if 6 <= len(s) <= 40 and s not in points:
                points.append(s)
                if len(points) >= 4:
                    break

    # 4. 财务术语提取（兜底）：从题干+解析+答案中匹配常见术语模式
    if len(points) < 3:
        corpus_parts = [stem or "", analysis or "", answer or ""]
        corpus = " ".join(corpus_parts)
        # 模式："X资本成本 / X利率 / X收益率 / X报酬率 / X系数 / X余额 / X百分比 / X补偿"
        # X 为 2-10 个汉字
        term_patterns = [
            r"[\u4e00-\u9fff]{2,10}资本成本",
            r"[\u4e00-\u9fff]{2,10}资本结构",
            r"[\u4e00-\u9fff]{2,10}实际利率",
            r"[\u4e00-\u9fff]{2,10}名义利率",
            r"[\u4e00-\u9fff]{2,10}收益率",
            r"[\u4e00-\u9fff]{2,10}报酬率",
            r"[\u4e00-\u9fff]{2,10}财务杠杆",
            r"[\u4e00-\u9fff]{2,10}经营杠杆",
            r"[\u4e00-\u9fff]{2,10}补偿性余额",
            r"[\u4e00-\u9fff]{2,10}调整模型",
            r"[\u4e00-\u9fff]{2,10}每股利润",
            r"[\u4e00-\u9fff]{2,10}无差别点",
            r"[\u4e00-\u9fff]{2,10}资金成本",
        ]
        for pat in term_patterns:
            if len(points) >= 3:
                break
            for m in re.finditer(pat, corpus):
                term = m.group(0).strip()
                if term and len(term) <= 12 and term not in points:
                    points.append(term)
                    if len(points) >= 3:
                        break

    return points[:8]


def _compute_id(chapter: str, num: int, type_enum: str, stem: str, source_pdf: str) -> str:
    """用章节 + 题号 + 题型 + 题干前 100 字 + 文件名生成稳定 hash。

    同一道题重复解析（不同时刻）应得到相同 id。
    """
    h = hashlib.sha256()
    h.update(chapter.encode("utf-8"))
    h.update(b"|")
    h.update(str(num).encode("utf-8"))
    h.update(b"|")
    h.update(type_enum.encode("utf-8"))
    h.update(b"|")
    h.update(stem[:100].encode("utf-8"))
    h.update(b"|")
    h.update(source_pdf.encode("utf-8"))
    return h.hexdigest()[:16]


def _parse_one_question(
    num: int,
    line_start: int,
    type_label: str,
    body: str,
    line_to_page: list[int],
    chapter: str,
    source_pdf: str,
) -> Question | None:
    """解析单道题，返回 Question 实例；失败返回 None 并写错误日志。"""
    type_enum = _type_label_to_enum(type_label)

    options, rest = _extract_options(body)
    raw_answer, analysis, knowledge_block = _extract_answer(rest, type_enum)

    if not raw_answer:
        _log_error(source_pdf, _slice_page_ref(line_to_page, line_start, 0),
                   f"题 {num} ({type_label}) 缺少答案字段")
        return None

    # 判断题自动补 ["对", "错"]
    options = _ensure_options_for_judge(type_enum, options)
    answer = _clean_answer_for_objective(type_enum, raw_answer, options)

    # 切题干：取选项之前的纯文本
    # 优先级：第一个选项行（A./A、） 之前的文本作为题干
    stem_match = re.split(r"\n\s*[A-Z][、.]", body, maxsplit=1)
    stem = stem_match[0].strip()
    # 兜底：题干中还可能含有"正确答案/我的答案/答案解析/知识点"残留
    # 用 _OPTION_BREAK_PREFIX_RE 切到这些前缀之前
    cut_match = _OPTION_BREAK_PREFIX_RE.search(stem)
    if cut_match:
        stem = stem[: cut_match.start()].strip()
    # 移除开头的题型标签残留
    stem = re.sub(r"^[\s（(]*(?:【\s*)?(?:单选题|多选题|判断题|填空题|计算分析题|综合题)(?:\s*】)?[\s）)]*",
                  "", stem).strip()
    # 移除题型后面的"（1.0分）"等分值标记
    stem = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", "", stem).strip()
    # 清洗 [PAGE=N] 跨页标记
    stem = _PAGE_MARK_RE.sub("", stem).strip()
    # 移除尾部孤立的"分/分）/分"等
    stem = re.sub(r"\s*分\s*$", "", stem).strip()
    if not stem:
        # 即测即评格式：题号. (单选题)题干 → body 开头就是题干
        # 但 _extract_options 已处理；如果到这里仍为空，说明题干确实缺失
        # 取 body 的前 200 字符
        stem = body[:200].strip().split("\n")[0]
    if not stem:
        _log_error(source_pdf, _slice_page_ref(line_to_page, line_start, 0),
                   f"题 {num} ({type_label}) 缺少题干")
        return None

    # 校验：客观题 options 必须非空
    if type_enum in {"single", "multi", "judge"} and not options:
        _log_error(source_pdf, _slice_page_ref(line_to_page, line_start, 0),
                   f"题 {num} ({type_label}) 客观题缺少 options")
        return None

    # 校验：答案是否在 options 中（仅客观题）
    if type_enum == "single" and options and answer not in {opt[0] for opt in options}:
        _log_error(source_pdf, _slice_page_ref(line_to_page, line_start, 0),
                   f"题 {num} ({type_label}) 答案 {answer!r} 不在选项 {options!r} 中")
        return None
    if type_enum == "multi" and options:
        valid_letters = {opt[0] for opt in options}
        if not all(c in valid_letters for c in answer):
            _log_error(source_pdf, _slice_page_ref(line_to_page, line_start, 0),
                       f"题 {num} ({type_label}) 多选答案 {answer!r} 含非法选项 {options!r}")
            return None
    if type_enum == "judge" and answer not in {"对", "错"}:
        _log_error(source_pdf, _slice_page_ref(line_to_page, line_start, 0),
                   f"题 {num} ({type_label}) 判断题答案 {answer!r} 非法")
        return None

    # key_points：仅主观题（calc/comprehensive）填充
    key_points: list[str] | None
    if type_enum in {"calc", "comprehensive"}:
        key_points = _extract_key_points(knowledge_block, analysis, answer, stem)
        if not key_points:
            _log_error(source_pdf, _slice_page_ref(line_to_page, line_start, 0),
                       f"题 {num} ({type_label}) 主观题未提取到 key_points")
            key_points = []
    else:
        key_points = None

    page_ref = _slice_page_ref(line_to_page, line_start, 0)
    qid = _compute_id(chapter, num, type_enum, stem, source_pdf)

    try:
        q = Question(
            id=qid,
            type=type_enum,
            chapter=chapter,
            number=num,
            stem=stem,
            options=options,
            answer=answer,
            key_points=key_points,
            analysis=analysis,
            difficulty=None,
            source_pdf=source_pdf,
            page_ref=page_ref,
        )
    except ValidationError as ve:
        _log_error(source_pdf, page_ref, f"题 {num} Pydantic 校验失败: {ve}")
        return None

    return q


# ---------------------------------------------------------------------------
# 单个 PDF 解析主流程
# ---------------------------------------------------------------------------

def parse_one_pdf(pdf_path: Path) -> list[Question]:
    """解析单个 PDF，返回 Question 列表（不写文件）。"""
    pdf_basename = pdf_path.name
    pages = extract_pdf_pages(pdf_path)
    joined_text, line_to_page = _normalize_text(pages)

    if not joined_text.strip():
        _log_error(pdf_basename, 1, "PDF 全文为空（可能含图片/扫描件）")
        return []

    try:
        chapter = detect_chapter(pdf_basename, joined_text)
    except ValueError as e:
        _log_error(pdf_basename, 1, str(e))
        return []

    type_groups = detect_type_groups(joined_text)
    questions = _split_questions(joined_text)

    # 用题型分组校验：若 PDF 含分组标记，则题号应落入对应分组范围
    # 但简化起见：信任 _split_questions + 每题的【类型】字段
    _ = type_groups  # 暂仅用于诊断

    out: list[Question] = []
    seen_ids: set[str] = set()
    for num, line_start, type_label, body in questions:
        q = _parse_one_question(num, line_start, type_label, body,
                                line_to_page, chapter, pdf_basename)
        if q is None:
            continue
        if q.id in seen_ids:
            # 同 id 说明重复解析（跨页拼接或同号多题型），保留第一个
            _log_error(pdf_basename, q.page_ref,
                       f"题号 {num} 重复（id={q.id}），已跳过")
            continue
        seen_ids.add(q.id)
        out.append(q)

    return out


# ---------------------------------------------------------------------------
# 写盘
# ---------------------------------------------------------------------------

def write_jsonl(questions: Iterable[Question], path: Path) -> int:
    """把 Question 列表写入 JSONL 文件，返回写入行数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(q.model_dump_json() + "\n")
            n += 1
    return n


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _safe_rel(path: Path, base: Path) -> Path:
    """relative_to 容错:若 path 不在 base 下,返回原 path。"""
    try:
        return path.relative_to(base)
    except ValueError:
        return path


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。

    向后兼容:不传任何参数 = 财务科目原 pipeline(PDF_DIR_DEFAULT + QUESTIONS_JSONL)。
    """
    parser = argparse.ArgumentParser(
        prog="parse_questions",
        description=(
            "PDF → questions.jsonl 解析器 "
            "(支持任意科目,默认=财务管理)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=PROJECT_ROOT / "财务管理资料",
        help="PDF 输入目录(默认 = 项目根/财务管理资料)",
    )
    parser.add_argument(
        "--subject",
        type=str,
        default="fin-mgmt",
        help="科目代码,用于输出文件命名(subject=fin-mgmt 走原 questions.jsonl;否则 = <subject>_questions_pdf.jsonl)",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=None,
        help="合并输出 JSONL 路径(None = 按 subject 自动决定)",
    )
    parser.add_argument(
        "--by-pdf-dir",
        type=Path,
        default=None,
        help="每 PDF 输出目录(None = data/parsed/by_pdf/)",
    )
    parser.add_argument(
        "--chapter-titles-json",
        type=Path,
        default=None,
        help="章节标题 JSON(可选;供后续科目注入)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析 PDF_DIR 下所有 PDF。

    使用方式:
        # 默认(财务科目,向后兼容)
        python -m packages.preprocessor.parse_questions
        # 公司战略科目
        python -m packages.preprocessor.parse_questions \\
            --pdf-dir '公司战略和风险管理' \\
            --subject corp-strat
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    pdf_dir: Path = args.pdf_dir
    subject: str = args.subject

    # 输出路径解析 —— subject=fin-mgmt 时维持原 questions.jsonl 输出路径(向后兼容)
    if args.output_jsonl is not None:
        merged_path: Path = args.output_jsonl
    elif subject == "fin-mgmt":
        merged_path = PARSED_DIR / "questions.jsonl"
    else:
        merged_path = PARSED_DIR / f"{subject}_questions_pdf.jsonl"

    by_pdf_dir: Path = args.by_pdf_dir or BY_PDF_DIR
    chapter_titles_path: Path | None = args.chapter_titles_json

    if not pdf_dir.exists():
        logger.error(f"PDF 目录不存在: {pdf_dir}")
        return 1

    pdf_paths = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_paths:
        logger.error(f"PDF 目录无 .pdf 文件: {pdf_dir}")
        return 1

    # 清空旧 errors.log（每次运行重新生成）
    if ERRORS_LOG.exists():
        ERRORS_LOG.unlink()

    # 清空旧输出 —— 只清空 by_pdf_dir 不清 merged_path(避免破坏 finance pipeline 现有产物)
    if by_pdf_dir.exists():
        for old in by_pdf_dir.glob("*.jsonl"):
            old.unlink()

    merged_path.parent.mkdir(parents=True, exist_ok=True)
    by_pdf_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "parse_questions: subject=%s pdf_dir=%s → %s",
        subject,
        _safe_rel(pdf_dir, PROJECT_ROOT),
        _safe_rel(merged_path, PROJECT_ROOT),
    )
    if chapter_titles_path is not None:
        logger.info("使用章节标题 JSON: %s", _safe_rel(chapter_titles_path, PROJECT_ROOT))

    all_questions: list[Question] = []
    failed_pdfs: list[tuple[str, str]] = []

    for pdf_path in pdf_paths:
        logger.info(f"开始解析 {pdf_path.name}")
        try:
            qs = parse_one_pdf(pdf_path)
        except Exception as e:  # noqa: BLE001
            logger.error(f"PDF {pdf_path.name} 解析异常: {e}")
            _log_error(pdf_path.name, 0, f"异常: {e!r}")
            failed_pdfs.append((pdf_path.name, str(e)))
            continue

        if not qs:
            failed_pdfs.append((pdf_path.name, "未提取到任何题目"))
            logger.warning(f"{pdf_path.name} 未提取到题目")
            continue

        out_path = by_pdf_dir / f"{pdf_path.stem}.jsonl"
        n = write_jsonl(qs, out_path)
        try:
            out_path_rel = out_path.relative_to(PROJECT_ROOT)
        except ValueError:
            out_path_rel = out_path
        logger.info(f"{pdf_path.name}: {n} 题 → {out_path_rel}")
        all_questions.extend(qs)

    # 合并输出
    if all_questions:
        n = write_jsonl(all_questions, merged_path)
        try:
            merged_rel = merged_path.relative_to(PROJECT_ROOT)
        except ValueError:
            merged_rel = merged_path
        logger.info(f"合并输出: {n} 题 → {merged_rel}")
    else:
        logger.warning(
            "未提取到任何题目(%d 个 PDF 输入),仍写空 JSONL 以记录 pipeline 状态",
            len(pdf_paths),
        )
        # 即使 0 题也写空 JSONL,便于下游 build_db.py 检测"已跑过 pipeline"
        merged_path.write_text("", encoding="utf-8")

    # 统计
    by_chapter: dict[str, int] = {}
    by_type: dict[str, int] = {}
    subjective_with_kp = 0
    subjective_without_kp = 0
    for q in all_questions:
        by_chapter[q.chapter] = by_chapter.get(q.chapter, 0) + 1
        by_type[q.type] = by_type.get(q.type, 0) + 1
        if q.type in {"calc", "comprehensive"}:
            if q.key_points and len(q.key_points) >= 3:
                subjective_with_kp += 1
            else:
                subjective_without_kp += 1

    # 关闭 errors.log
    global _ERRORS_FILE_HANDLE
    if _ERRORS_FILE_HANDLE is not None:
        _ERRORS_FILE_HANDLE.close()
        _ERRORS_FILE_HANDLE = None

    # 最终报告（写 stdout + 也可被 orchestrator 抓取）
    print("\n" + "=" * 70)
    print("解析完成报告")
    print("=" * 70)
    print(f"合并 JSONL 总行数: {len(all_questions)}")
    print(f"按章节: {dict(sorted(by_chapter.items()))}")
    print(f"按题型: {dict(sorted(by_type.items()))}")
    subjective_total = by_type.get("calc", 0) + by_type.get("comprehensive", 0)
    print(f"主观题总数 (calc + comprehensive): {subjective_total}")
    print(f"  含 key_points (≥3): {subjective_with_kp}")
    print(f"  缺失 key_points: {subjective_without_kp}")
    if failed_pdfs:
        print(f"解析失败的 PDF: {len(failed_pdfs)}")
        for name, reason in failed_pdfs:
            print(f"  - {name}: {reason}")
    else:
        print("解析失败 PDF: 无")

    if ERRORS_LOG.exists():
        size = ERRORS_LOG.stat().st_size
        print(f"errors.log: {size} bytes (含 {sum(1 for _ in open(ERRORS_LOG, encoding='utf-8'))} 条记录)")

    return 0


if __name__ == "__main__":
    sys.exit(main())