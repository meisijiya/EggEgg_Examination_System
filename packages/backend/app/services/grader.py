"""判分 Service — 客观题选项集合对比 + 主观题关键词覆盖率。

按 spec §6.4：
- 客观题（单选/多选/判断）：选项集合对比，相等才算对
- 主观题（计算/综合）：key_points 关键词覆盖率 = matched/total × max_score
  - 覆盖率 ≥ 1.0 → 满分
  - 0.6 ≤ 覆盖率 < 1.0 → 按比例（MIN_COVERAGE 可调）
  - 覆盖率 < 0.6 → 0 分
- 答案 < 5 字 → 0 分 + 评语"答案过短"
- key_points 为空（数据缺失）→ 退化为"答案完全等于参考答案"

主观题扩展（fix-17）：
- 自动拆解"1.x；2.y；..."编号格式为 sub_answers 列表
- key_point 在 sub_answers 联合中匹配（任一 sub 命中即覆盖）
- 评语追加"识别到 N 个分小问作答" + 列出未覆盖要点
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, List

# ---------- 中文停用词表（小型，覆盖最常见干扰词） ----------

STOP_WORDS: frozenset[str] = frozenset(
    {
        # 助词 / 介词 / 连词
        "的", "了", "是", "在", "和", "与", "或", "及", "等", "并",
        "为", "以", "于", "从", "到", "向", "把", "被", "让",
        "而", "但", "则", "因", "所", "其", "此", "那", "各", "某",
        "上", "下", "中", "内", "外", "前", "后", "里",
        # 代词
        "我", "你", "他", "她", "它", "我们", "你们", "他们", "它们",
        "这", "那", "这个", "那个", "这些", "那些", "自己",
        # 常见虚词
        "也", "都", "再", "又", "已", "已经", "正在", "将", "会", "能",
        "可以", "可能", "应该", "必须", "需要",
        # 标点（单字符过滤会同时去除）
        "，", "。", "、", "；", "：", "？", "！", """, """, "'", "'",
        "（", "）", "(", ")", "【", "】", "[", "]", "《", "》",
        # 数字单位（避免 "1" "2" 误判）
        "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    }
)


@dataclass
class GradedAnswer:
    """判分结果。"""

    awarded_score: float
    is_correct: bool | None  # 客观 True/False，主观 None 或 True/False
    comment: str
    # 主观题扩展字段（客观题为 None）
    sub_answer_count: int | None = None  # 识别到的分小问作答数
    missed_points: list[str] | None = None  # 未覆盖关键要点（最多 3 条）


# ---------- sub_answer 拆解（fix-17）----------

# 段首"数字 + 分隔符"识别（兼容 `.`、`、`、`)`、空格）
# 注意：(?!\d) 排除小数点延续（如 "4.5%" 里的 `.` 不应被当成编号分隔符）
_NUMBERED_PREFIX = re.compile(r"^\s*\d+\s*[\.、)\s](?!\d)")


def parse_sub_answers(user_answer: str) -> List[str]:
    """拆解用户答案为 sub_answers 列表。

    支持三种格式（按优先级判定）：
      A) 编号格式：至少 2 段以"数字+分隔符"开头（如 "1.xxx；2.xxx；3.xxx"）
         →  按编号拆，剥离每段开头的编号前缀
      B) 分号分隔：无编号但有 "；"/";" 切分（如 "xxx；xxx；xxx"）
         →  按分隔符原样拆
      C) 整段无分隔：返回 [text]

    Args:
        user_answer: 用户主观题答案原始字符串。

    Returns:
        非空 sub_answer 字符串列表。空串 / 过短 (< 5 字) → 空列表。
    """
    if not user_answer or len(user_answer.strip()) < 5:
        return []
    text = user_answer.strip()

    # 含中文/英文分号：先按分隔符切，再判定是否编号格式
    if "；" in text or ";" in text:
        raw_parts = re.split(r"[；;]", text)
        raw_parts = [p.strip() for p in raw_parts if p.strip()]
        if len(raw_parts) >= 2:
            numbered_count = sum(1 for p in raw_parts if _NUMBERED_PREFIX.match(p))
            if numbered_count >= 2:
                # 编号格式：剥离每段开头的编号前缀
                cleaned: list[str] = []
                for p in raw_parts:
                    m = _NUMBERED_PREFIX.match(p)
                    if m:
                        cleaned.append(p[m.end() :].strip())
                    else:
                        cleaned.append(p)
                return [c for c in cleaned if c]
            # 仅按分号切（无编号）
            return raw_parts

    # C) 整段
    return [text]


# ---------- 工具 ----------


def _normalize_options(text: str) -> set[str]:
    """把"ABD"/"A,B,D"/"ABD "统一归一化为 {'A','B','D'}。"""
    if not text:
        return set()
    cleaned = re.sub(r"[\s,，;；、|/\\]+", "", text.upper())
    return set(cleaned)


def _tokenize_chinese(text: str) -> list[str]:
    """简单中文分词 — 单字 + 2-gram。

    不用 jieba（避免引入重型依赖）。
    返回 token 列表（含 1-char 和 2-char gram）。
    """
    if not text:
        return []
    # 去标点 / 停用词 / 空白
    chars: list[str] = []
    for ch in text:
        if ch in STOP_WORDS:
            continue
        if ch.isspace():
            continue
        chars.append(ch)
    if not chars:
        return []
    # 1-gram + 2-gram
    tokens = list(chars)
    for i in range(len(chars) - 1):
        tokens.append(chars[i] + chars[i + 1])
    return tokens


def _remove_stopwords(text: str) -> str:
    """去停用词 + 标点。"""
    return "".join(ch for ch in text if ch not in STOP_WORDS and not ch.isspace())


def _key_point_covered(kp: str, user_text: str) -> bool:
    """判断关键要点是否被用户答案覆盖。

    判定规则：key_point 作为子串出现在 user_text 中（去停用词后）。
    - 长度 ≥ 2 的 key_point 才参与匹配（避免单字误判）
    - 这样 "市场利率" 不会因为 "利率" 二字组就误判覆盖
    """
    kp_clean = "".join(ch for ch in kp if ch not in STOP_WORDS and not ch.isspace())
    if len(kp_clean) < 2:
        # 单字要点降级为 token 匹配
        tokens = _tokenize_chinese(user_text)
        return kp_clean in tokens
    return kp_clean in user_text


# ---------- 主入口 ----------


def grade_answer(
    q_type: str,
    correct_answer: str,
    user_answer: str | None,
    full_score: float,
    key_points: list[str] | None = None,
    min_coverage: float = 0.6,
) -> GradedAnswer:
    """判分主入口。

    参数:
        q_type: single / multi / judge / calc / comprehensive
        correct_answer: 正确答案文本
            - single: "A"
            - multi: "ABD" (任意分隔)
            - judge: "对" / "错"（或 "A"/"B" 之类）
            - calc / comprehensive: 参考答案文本
        user_answer: 学员答案（空串/None 等价"未作答"）
        full_score: 题目满分
        key_points: 主观题关键要点列表（客观题可传 None）
        min_coverage: 主观题最低覆盖率门槛（默认 0.6）

    返回:
        GradedAnswer: 含 awarded_score / is_correct / comment
    """
    # ---- 通用：未作答 ----
    if user_answer is None or user_answer.strip() == "":
        return GradedAnswer(
            awarded_score=0.0, is_correct=False, comment="未作答"
        )

    # ---- 客观题 ----
    if q_type in ("single", "multi", "judge"):
        return _grade_objective(q_type, correct_answer, user_answer, full_score)

    # ---- 主观题 ----
    return _grade_subjective(
        correct_answer=correct_answer,
        user_answer=user_answer,
        full_score=full_score,
        key_points=key_points,
        min_coverage=min_coverage,
    )


def _grade_objective(
    q_type: str,
    correct_answer: str,
    user_answer: str,
    full_score: float,
) -> GradedAnswer:
    """客观题判分：选项集合相等才算对。"""
    correct_set = _normalize_options(correct_answer)
    given_set = _normalize_options(user_answer)

    is_correct = bool(correct_set) and (correct_set == given_set)

    if is_correct:
        return GradedAnswer(
            awarded_score=full_score,
            is_correct=True,
            comment="回答正确",
        )
    return GradedAnswer(
        awarded_score=0.0,
        is_correct=False,
        comment=f"正确答案：{correct_answer}",
    )


def _grade_subjective(
    correct_answer: str,
    user_answer: str,
    full_score: float,
    key_points: list[str] | None,
    min_coverage: float,
) -> GradedAnswer:
    """主观题判分：按编号拆解 sub_answers + 关键词覆盖率 + 未覆盖提示。

    流程：
      1) 短答 (< 5 字) → 0 分
      2) key_points 缺失 → 退化为参考答案完全匹配
      3) parse_sub_answers 拆 sub_answers（编号 / 分号 / 整段）
      4) 联合所有 sub_answer 做关键词匹配（任一 sub 命中即视为覆盖）
      5) 覆盖率 ≥ 1.0 → 满分；≥ min_coverage → 按比例；否则 0
      6) 评语追加"识别到 N 个分小问作答" + 列出未覆盖要点（最多 3 条，截断 80 字）
    """
    stripped = user_answer.strip()

    # 1. 门槛：答案过短
    if len(stripped) < 5:
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment="答案过短，无法评估",
        )

    # 2. 退化路径：key_points 为空 → 退化为参考答案完全一致
    if not key_points:
        if stripped == correct_answer.strip():
            return GradedAnswer(
                awarded_score=full_score,
                is_correct=True,
                comment="答案与参考答案一致",
            )
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment="关键要点缺失，无法评估",
        )

    # 3. 拆 sub_answers（编号 / 分号 / 整段）
    sub_answers = parse_sub_answers(stripped)
    n_sub = len(sub_answers)

    # 4. 联合所有 sub_answer 做关键词匹配（任一 sub 命中即覆盖）
    joined = " ".join(sub_answers)
    user_clean = _remove_stopwords(joined)
    matched_points = [kp for kp in key_points if _key_point_covered(kp, user_clean)]
    matched = len(matched_points)
    total = len(key_points)
    coverage = matched / total if total else 0

    # 5. 算分 + 评语主体
    if coverage >= 1.0:
        score = full_score
        prefix = f"完整覆盖所有关键要点（{matched}/{total}）"
    elif coverage >= min_coverage:
        score = round(full_score * coverage, 1)
        prefix = f"覆盖 {matched}/{total} 个关键要点（{coverage * 100:.0f}%）"
    else:
        score = 0.0
        prefix = (
            f"仅覆盖 {matched}/{total} 个关键要点，"
            f"未达 {min_coverage * 100:.0f}% 门槛"
        )

    sub_hint = f"，识别到 {n_sub} 个分小问作答" if n_sub >= 2 else ""
    comment = f"{prefix}{sub_hint}"

    # 6. 列出未覆盖关键要点（最多 3 条，截断 80 字）
    missed_points: list[str] | None = None
    if score < full_score and total > matched:
        missed = [kp for kp in key_points if kp not in matched_points][:3]
        if missed:
            truncated = "；".join(missed)
            if len(truncated) > 80:
                truncated = truncated[:80] + "…"
            comment += f"。未覆盖：{truncated}"
            missed_points = missed

    return GradedAnswer(
        awarded_score=score,
        is_correct=score >= full_score * 0.6,
        comment=comment,
        sub_answer_count=n_sub if n_sub >= 2 else None,
        missed_points=missed_points,
    )


# ---------- 辅助：从 question 行解析 ----------


def parse_key_points(s: str | None) -> list[str] | None:
    """从 DB 存的 JSON 字符串解析 key_points 列表。"""
    if not s:
        return None
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def parse_options(s: str | None) -> list[str] | None:
    """从 DB 存的 JSON 字符串解析 options 列表。"""
    if not s:
        return None
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, TypeError):
        pass
    return None