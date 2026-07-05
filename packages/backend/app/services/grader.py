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

fix-30 P0 扩展：5 题型判分支持
- `short_answer`:复用 _grade_subjective 的覆盖率逻辑,但不拆 sub_answers(< 200 字最佳)
- `case_analysis`:新增独立函数,按结构化 rubric(sub_questions + conclusion)逐项打分

Phase 2-Lane-B:评语层改为 PraiseService 池化生成
- 所有 _grade_* / grade_answer 入口不再硬编码"回答正确"/"正确答案:X"等字符串
- 改为调 praise_service.pick(scenario) 随机鼓励语
- scenario 决策:
  * user_answer is None/empty → 'unanswered'
  * 满分(coverage >= 1.0 / 单选答对 / 判断答对) → 'correct'
  * 其余(覆盖不足 / 答错 / 过短 / key_points 缺失) → 'wrong'
- GradedAnswer.correct_answer 字段保留(独立字段,前端 ExamResult 显示答案)
- comment 字段不再含"正确答案:X"内容(避免重复展示)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping

from app.services.praise_service import get_praise_service

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
    # fix-30 P1:案例分析题按 sub_question 拆解的明细分(仅 case_analysis 有值)
    # 每项 dict 含 id / points / awarded / matched / total / coverage / missed_points
    per_sub_question_scores: list[dict[str, Any]] | None = None


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


def parse_sub_answers_to_map(
    user_answer: str,
    rubric: Mapping[str, Any] | None,
) -> dict[str, str]:
    """按 rubric sub_questions 顺序切分 user_answer → {sub_id: sub_text}。

    关键语义（fix-30 P0 重构,逐步退化,优先精度）:
      1. parse_sub_answers 按编号/分号切 user_answer 为有序列表 parts
      2. parts 长 == rubric.sub_questions 长 → 1-1 位置对应(精确路径)
      3. parts 长 ≥ 2 但 < rubric.sub_questions 数 → 第 1 段=sub[0],
         第 2 段=sub[1],剩余 sub 拿空串(漏答 fallback 整段)
      4. parts 长 == 1(无可识别分隔) → 整段对**所有** sub 做完整 fallback
         (等同原"整段对所有 sub 算同一 coverage"行为)
      5. parts 长 == 0 / parse 失败 → {} (fallback 整段在 grader 内做)

    Args:
        user_answer: 学员答案原始字符串。
        rubric: dict 格式(含 "sub_questions" list),可能为 None。

    Returns:
        dict {sub_id: sub_text};rubric 为 None 或无 sub_questions → {}
    """
    if not rubric:
        return {}
    sub_questions = rubric.get("sub_questions") or []
    if not sub_questions:
        return {}

    parts = parse_sub_answers(user_answer)

    # 退化 1:parse 失败(< 5 字或无内容)→ 整段对所有 sub(在 grader 内做 fallback)
    if not parts:
        return {}

    valid_subs = [
        (str(sq.get("id", idx + 1)), sq)
        for idx, sq in enumerate(sub_questions)
        if isinstance(sq, Mapping)
    ]
    n_valid = len(valid_subs)

    # 退化 2:parts len == 1(无可识别的编号/分号分隔)→ 整段对所有 sub
    if len(parts) == 1:
        return {sq_id: parts[0] for sq_id, _ in valid_subs}

    # 标准路径:按位置对应
    out: dict[str, str] = {}
    for idx, (sq_id, _) in enumerate(valid_subs):
        if idx < len(parts):
            out[sq_id] = parts[idx]
        else:
            # 漏答该 sub → "" 让 grader 内部 fallback 整段
            out[sq_id] = ""
    # parts 多了也忽略(excess 用户文本会被丢掉)
    return out


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


# ---------- 共享覆盖率计算（fix-30 P0）----------


def _compute_coverage(
    user_clean: str, key_points: list[str]
) -> tuple[float, list[str]]:
    """计算 key_points 在 user_clean 中的覆盖率 + 命中列表。

    fix-30 P0:_grade_subjective 和 _grade_short_answer 共用的核心算法。
    - 对每个 key_point 判定是否在 user_clean 中出现(去停用词后子串匹配)
    - 覆盖率 = matched / total;空 key_points 时返回 (0, [])

    参数:
        user_clean: 已去停用词的用户答案字符串
        key_points: 题目关键要点列表

    返回:
        (coverage, matched_points)
        - coverage: 浮点 0-1
        - matched_points: 命中要点子集(保持原顺序)
    """
    matched_points = [kp for kp in key_points if _key_point_covered(kp, user_clean)]
    total = len(key_points)
    coverage = matched_points.__len__() / total if total else 0
    return coverage, matched_points


def _build_comment_by_coverage(
    full_score: float,
    key_points: list[str],
    matched_points: list[str],
    coverage: float,
    min_coverage: float,
    extra_comment: str = "",
) -> tuple[str, list[str] | None]:
    """按覆盖率生成评语主体 + 未覆盖要点列表。

    Phase 2-Lane-B 改造:
    - 评语主体改为 PraiseService 池化生成(场景由 coverage 决定)
    - 不再硬编码"完整覆盖..."/"覆盖 N/M..."等覆盖率描述
    - coverage ≥ 1.0 → scenario='correct'
    - else → scenario='wrong'(覆盖不足 / 答错)
    - 未覆盖要点列表仍在 `missed_points` 字段返回(供前端独立展示)
    - extra_comment 参数保留以兼容 _grade_short_answer 长度警告(当前 unused,
      PraiseService 不消费 extra_comment;保留参数避免 caller 大改)

    ponytail: 之前是覆盖率描述字符串(含 "N/M" / "X%" 数字),
    现在是鼓励话语 + missed_points 单独字段。前端可结合两者展示
    (comment 显示鼓励语,missed_points 展示未覆盖要点列表)。

    返回:
        (praise_comment, missed_points_list)
        - praise_comment: 从 praise pool 随机 pick 的鼓励语
        - missed_points_list: 最多 3 条未覆盖要点;满分或全覆盖时为 None
    """
    matched = len(matched_points)
    total = len(key_points)

    # 场景决策:满分 → correct,其余 → wrong
    scenario = "correct" if coverage >= 1.0 else "wrong"
    praise = get_praise_service()
    comment = praise.pick(scenario=scenario)

    # 未覆盖要点列表(独立字段,前端按需展示)
    missed_points: list[str] | None = None
    if coverage < 1.0 and total > matched:
        missed = [kp for kp in key_points if kp not in matched_points][:3]
        if missed:
            missed_points = missed

    return comment, missed_points


# ---------- 主入口 ----------


def grade_answer(
    q_type: str,
    correct_answer: str,
    user_answer: str | None,
    full_score: float,
    key_points: list[str] | None = None,
    min_coverage: float = 0.6,
    rubric: Mapping[str, Any] | None = None,
) -> GradedAnswer:
    """判分主入口。

    参数:
        q_type: single / multi / judge / calc / comprehensive /
                short_answer / case_analysis
        correct_answer: 正确答案文本
            - single: "A"
            - multi: "ABD" (任意分隔)
            - judge: "对" / "错"（或 "A"/"B" 之类）
            - calc / comprehensive / short_answer: 参考答案文本
            - case_analysis: 整体参考答案(grader 主要依赖 rubric 逐项打分)
        user_answer: 学员答案（空串/None 等价"未作答"）
        full_score: 题目满分
        key_points: 主观题关键要点列表（客观题 / case_analysis 可传 None）
        min_coverage: 主观题最低覆盖率门槛（默认 0.6）
        rubric: 案例分析题结构化评分 rubric(仅 case_analysis 必传)
            形如 {"sub_questions": [...], "conclusion": {...}}

    返回:
        GradedAnswer: 含 awarded_score / is_correct / comment /
        sub_answer_count(主观题扩展) / missed_points / per_sub_question_scores(case_analysis)
    """
    # ---- 通用：未作答 ----
    if user_answer is None or user_answer.strip() == "":
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment=get_praise_service().pick(scenario="unanswered"),
        )

    # ---- 客观题 ----
    if q_type in ("single", "multi", "judge"):
        return _grade_objective(q_type, correct_answer, user_answer, full_score)

    # ---- 案例分析题(独立函数,不走通用覆盖率)----
    if q_type == "case_analysis":
        return _grade_case_analysis(
            user_answer=user_answer,
            rubric=rubric,
            full_score=full_score,
            min_coverage=min_coverage,
        )

    # ---- 短答题(轻量,不拆 sub_answers;< 200 字最佳)----
    if q_type == "short_answer":
        return _grade_short_answer(
            correct_answer=correct_answer,
            user_answer=user_answer,
            full_score=full_score,
            key_points=key_points,
            min_coverage=min_coverage,
        )

    # ---- 计算/综合题(含 sub_answer 拆解)----
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
    """客观题判分：选项集合相等才算对。

    Phase 2-Lane-B 改造:
    - comment 改为 PraiseService 池化生成(不再含"正确答案:X"内容)
    - 答对 → scenario='correct',答错 → scenario='wrong'
    - GradedAnswer.correct_answer 字段保留(独立,前端 ExamResult 显示)
    """
    correct_set = _normalize_options(correct_answer)
    given_set = _normalize_options(user_answer)

    is_correct = bool(correct_set) and (correct_set == given_set)

    scenario = "correct" if is_correct else "wrong"
    return GradedAnswer(
        awarded_score=full_score if is_correct else 0.0,
        is_correct=is_correct,
        comment=get_praise_service().pick(scenario=scenario),
    )


def _grade_subjective(
    correct_answer: str,
    user_answer: str,
    full_score: float,
    key_points: list[str] | None,
    min_coverage: float,
) -> GradedAnswer:
    """主观题判分(calc/comprehensive):按编号拆解 sub_answers + 关键词覆盖率 + 未覆盖提示。

    流程：
      1) 短答 (< 5 字) → 0 分(评语用 praise 'wrong' 场景)
      2) key_points 缺失 → 退化为参考答案完全匹配(评语用 praise)
      3) parse_sub_answers 拆 sub_answers（编号 / 分号 / 整段）
      4) 联合所有 sub_answer 做关键词匹配（任一 sub 命中即覆盖）
      5) 覆盖率 ≥ 1.0 → 满分；≥ min_coverage → 按比例；否则 0
      6) 评语:Phase 2-Lane-B 改为 PraiseService 池化(无"识别到 N 个分小问" 文本)
         — sub_answer_count 字段仍保留,前端可独立展示

    Phase 2-Lane-B 改造:
    - 评语不再含"识别到 N 个分小问作答" + "未覆盖:..."(改由 sub_answer_count /
      missed_points 独立字段承担)
    - comment 全部走 PraiseService 池化

    注:fix-30 P0 重构后内部调用 _compute_coverage + _build_comment_by_coverage 共享 helper。
    """
    stripped = user_answer.strip()
    praise = get_praise_service()

    # 1. 门槛：答案过短
    if len(stripped) < 5:
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment=praise.pick(scenario="wrong"),
        )

    # 2. 退化路径：key_points 为空 → 退化为参考答案完全一致
    if not key_points:
        if stripped == correct_answer.strip():
            return GradedAnswer(
                awarded_score=full_score,
                is_correct=True,
                comment=praise.pick(scenario="correct"),
            )
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment=praise.pick(scenario="wrong"),
        )

    # 3. 拆 sub_answers（编号 / 分号 / 整段）
    sub_answers = parse_sub_answers(stripped)
    n_sub = len(sub_answers)

    # 4. 联合所有 sub_answer 做关键词匹配（任一 sub 命中即覆盖）
    joined = " ".join(sub_answers)
    user_clean = _remove_stopwords(joined)
    coverage, matched_points = _compute_coverage(user_clean, key_points)

    # 5. 评语主体（共享 helper,Phase 2-Lane-B 改为 PraiseService 池化）
    comment, missed_points = _build_comment_by_coverage(
        full_score=full_score,
        key_points=key_points,
        matched_points=matched_points,
        coverage=coverage,
        min_coverage=min_coverage,
    )

    # 6. 算分（共用阈值）
    if coverage >= 1.0:
        score = full_score
    elif coverage >= min_coverage:
        score = round(full_score * coverage, 1)
    else:
        score = 0.0

    return GradedAnswer(
        awarded_score=score,
        is_correct=score >= full_score * 0.6,
        comment=comment,
        sub_answer_count=n_sub if n_sub >= 2 else None,
        missed_points=missed_points,
    )


def _grade_short_answer(
    correct_answer: str,
    user_answer: str,
    full_score: float,
    key_points: list[str] | None,
    min_coverage: float,
) -> GradedAnswer:
    """短答题判分(轻量级主观题)— fix-30 P0 + Phase 2-Lane-B praise 池化。

    与 _grade_subjective 的差异:
    - 不拆 sub_answers(短答题是单一短段,不应拆"1.x；2.y")
    - < 200 字最佳,> 200 字软警告(Phase 2-Lane-B 后警告不再注入 comment)
    - 其余(key_points 覆盖率、min_coverage 阈值、满分/按比例/0 分三档)完全复用

    Phase 2-Lane-B 改造:
    - 评语全部走 PraiseService 池化(不再含"答案偏长,建议精简"等硬编码文本)
    - length_warning 软警告暂不暴露到 comment(若需要后续可通过 missed_points
      之类独立字段呈现,或前端基于 user_answer 长度自行判定)

    流程与 _grade_subjective 完全平行:
      1) < 5 字 → 0 分(评语 'wrong')
      2) key_points 缺失 → 退化为参考答案完全匹配(评语 'correct' / 'wrong')
      3) 不拆 sub,直接对整段计算 coverage
      4) 评语 + 算分走共享 helper(praise 池化)
    """
    stripped = user_answer.strip()
    praise = get_praise_service()

    # 1. 门槛
    if len(stripped) < 5:
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment=praise.pick(scenario="wrong"),
        )

    # 2. key_points 缺失 → 退化
    if not key_points:
        if stripped == correct_answer.strip():
            return GradedAnswer(
                awarded_score=full_score,
                is_correct=True,
                comment=praise.pick(scenario="correct"),
            )
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment=praise.pick(scenario="wrong"),
        )

    # 3. 不拆 sub_answers,直接对整段计算 coverage
    user_clean = _remove_stopwords(stripped)
    coverage, matched_points = _compute_coverage(user_clean, key_points)

    # 4. 评语 + 算分（共享 helper,Phase 2-Lane-B 改为 PraiseService 池化）
    comment, missed_points = _build_comment_by_coverage(
        full_score=full_score,
        key_points=key_points,
        matched_points=matched_points,
        coverage=coverage,
        min_coverage=min_coverage,
    )

    if coverage >= 1.0:
        score = full_score
    elif coverage >= min_coverage:
        score = round(full_score * coverage, 1)
    else:
        score = 0.0

    return GradedAnswer(
        awarded_score=score,
        is_correct=score >= full_score * 0.6,
        comment=comment,
        sub_answer_count=None,  # short_answer 不拆 sub
        missed_points=missed_points,
    )


def _grade_case_analysis(
    user_answer: str,
    rubric: Mapping[str, Any] | None,
    full_score: float,
    min_coverage: float = 0.6,
) -> GradedAnswer:
    """案例分析题判分(结构化 rubric)— fix-30 P0 critical。

    与 _grade_subjective 完全独立(避免污染主观题既有路径):
    - 依赖 rubric 结构而非单一 key_points 列表
    - rubric 缺失 → 0 分 + "缺少评分 rubric"
    - 按 sub_questions 逐项打分(key_points 覆盖率 × points)
    - conclusion 独立打分(criteria 覆盖率 × points)
    - 总分 cap 到 full_score(防止 rubric 配错导致超分)
    - 输出 per_sub_question_scores 明细(供前端展示子问题得分)

    rubric 结构(Pydantic CaseRubric.model_dump 序列化形):
        {
            "sub_questions": [
                {"id": "1", "points": 3, "key_points": ["SWOT", "PEST"]},
                {"id": "2", "points": 4, "key_points": ["战略选择"]}
            ],
            "conclusion": {"points": 3, "criteria": ["总结性结论", "可执行建议"]}
        }
    """
    # 1. rubric 缺失 → 不可评分(Phase 2-Lane-B:评语用 wrong 池化)
    if not rubric:
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment=get_praise_service().pick(scenario="wrong"),
            per_sub_question_scores=[],
        )

    sub_questions_raw = rubric.get("sub_questions") or []
    conclusion_raw = rubric.get("conclusion")

    # rubric 退化：既无 sub_questions 也无 conclusion → 无法评分
    if not sub_questions_raw and not conclusion_raw:
        return GradedAnswer(
            awarded_score=0.0,
            is_correct=False,
            comment=get_praise_service().pick(scenario="wrong"),
            per_sub_question_scores=[],
        )

    # 2. 按 sub_id 切分 user_answer(fix-30 P0 精度改进)
    sub_ans_map = parse_sub_answers_to_map(user_answer, rubric)
    full_user_clean = _remove_stopwords(user_answer)  # fallback 给整段评分
    per_sub: list[dict[str, Any]] = []
    total_score = 0.0
    total_possible = 0.0

    # 3. 逐 sub_question 用专属 sub 答案算 coverage
    for sq in sub_questions_raw:
        if not isinstance(sq, Mapping):
            continue
        sq_id = str(sq.get("id", "?"))
        sq_points = float(sq.get("points", 0) or 0)
        kps_raw = sq.get("key_points") or []
        kps = [str(k) for k in kps_raw if isinstance(k, (str, int, float))]
        total_possible += sq_points

        # 用户子答案(切分后)或整段 fallback
        user_sub_text = sub_ans_map.get(sq_id, "").strip()
        if user_sub_text:
            user_sub_clean = _remove_stopwords(user_sub_text)
            coverage_src = user_sub_clean if kps else (user_sub_clean or full_user_clean)
        else:
            # 学员漏答此 sub → 用整段算(避免完全 0 分,宽松)
            coverage_src = full_user_clean

        if kps:
            coverage_sq, matched_sq = _compute_coverage(coverage_src, kps)
        else:
            # 无 key_points → 退化:整段非空即满分
            coverage_sq = 1.0 if user_answer.strip() else 0.0
            matched_sq = []

        sq_score = round(sq_points * coverage_sq, 1)
        total_score += sq_score

        missed_sq = (
            [kp for kp in kps if kp not in matched_sq]
            if len(matched_sq) < len(kps)
            else []
        )
        per_sub.append(
            {
                "id": sq_id,
                "points": sq_points,
                "awarded": sq_score,
                "matched": len(matched_sq),
                "total": len(kps),
                "coverage": coverage_sq,
                "missed_points": missed_sq,
            }
        )

    # 4. conclusion 独立打分(用整段)
    if isinstance(conclusion_raw, Mapping):
        c_points = float(conclusion_raw.get("points", 0) or 0)
        c_criteria_raw = conclusion_raw.get("criteria") or []
        c_criteria = [
            str(c) for c in c_criteria_raw if isinstance(c, (str, int, float))
        ]
        total_possible += c_points

        if c_criteria:
            coverage_c, matched_c = _compute_coverage(full_user_clean, c_criteria)
        else:
            coverage_c = 1.0 if user_answer.strip() else 0.0
            matched_c = []

        c_score = round(c_points * coverage_c, 1)
        total_score += c_score

        missed_c = (
            [c for c in c_criteria if c not in matched_c]
            if len(matched_c) < len(c_criteria)
            else []
        )
        per_sub.append(
            {
                "id": "conclusion",
                "points": c_points,
                "awarded": c_score,
                "matched": len(matched_c),
                "total": len(c_criteria),
                "coverage": coverage_c,
                "missed_points": missed_c,
            }
        )

    # 4. 防止 rubric 配错导致超分
    if total_possible > 0 and total_score > full_score:
        total_score = full_score

    # 5. 评语 + is_correct(Phase 2-Lane-B:评语改 PraiseService 池化)
    if total_possible > 0:
        overall_coverage = total_score / total_possible
    else:
        # 极端兜底:rubric 所有 points=0 → 满分(无评分项)
        overall_coverage = 1.0

    # Phase 2-Lane-B:不再硬编码覆盖率描述,改 PraiseService 池化评语
    scenario = "correct" if overall_coverage >= 1.0 else "wrong"
    comment = get_praise_service().pick(scenario=scenario)

    return GradedAnswer(
        awarded_score=total_score,
        is_correct=overall_coverage >= min_coverage,
        comment=comment,
        sub_answer_count=None,  # case_analysis 用 per_sub_question_scores 替代
        missed_points=None,
        per_sub_question_scores=per_sub,
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