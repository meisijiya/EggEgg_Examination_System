"""出题 Service — 章节×题型×难度 三维加权抽样。

按 spec §6.2 / §6.3：
- 试卷规格：单 15×2 + 多 10×3 + 判 10×1 + 计 4×5 + 综 2×10 = 100 分 / 41 题
- 难度分布：easy 30% / medium 50% / hard 20%
- 至少覆盖 9 章中的 8 章
- 缺口 fallback：随机抽全题型兜底池

模式支持（fix-22 引入，fix-22 P0 完善）：
- `mode='standard'`：走原 `PaperAssembler.assemble()` 流程
- `mode='mixed'`：fix-20 真实 AI 改编（~30% 题改编 + 防幻觉护栏）
  fix-22 P0 优化：asyncio.gather 并发执行改编（DeepSeek API 调用并发），
  从串行 60-96s 降到 ~10-15s。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_session_factory
from app.models.question import Chapter, Question

logger = logging.getLogger("fes.paper_assembler")

# ---------- 模式常量 ----------


SUPPORTED_MODES: tuple[str, ...] = ("standard", "mixed")


def validate_mode(mode: str) -> str:
    """校验 mode 取值；非法时回退 standard（防御性）。

    参数:
        mode: 调用方传入的字符串。
    返回:
        校验后的合法 mode。
    """
    if mode not in SUPPORTED_MODES:
        logger.warning("未知 mode=%s，回退 standard", mode)
        return "standard"
    return mode

# ---------- 试卷规格 ----------


@dataclass(frozen=True)
class QuestionSlot:
    """试卷中的一个槽位（题型 + 分值）。"""

    type: str  # single/multi/judge/calc/comprehensive
    score: float  # 该题分值


@dataclass(frozen=True)
class PaperSpec:
    """试卷规格定义。"""

    slots: tuple[QuestionSlot, ...]
    time_limit_minutes: int = 120
    difficulty_target: dict[str, float] = field(
        default_factory=lambda: {"easy": 0.30, "medium": 0.50, "hard": 0.20}
    )
    min_chapter_coverage: int = 8  # 至少覆盖章节数（9 章 → 至少 8）

    @property
    def total_score(self) -> float:
        return sum(s.score for s in self.slots)

    @property
    def total_questions(self) -> int:
        return len(self.slots)

    def slots_by_type(self) -> dict[str, list[QuestionSlot]]:
        """按题型分组槽位。"""
        out: dict[str, list[QuestionSlot]] = defaultdict(list)
        for s in self.slots:
            out[s.type].append(s)
        return dict(out)


def build_default_spec() -> PaperSpec:
    """构建默认试卷规格（按 spec §6.2）。"""
    slots: list[QuestionSlot] = []
    # 单选 15 道 × 2 分
    slots.extend(QuestionSlot("single", 2.0) for _ in range(15))
    # 多选 10 道 × 3 分
    slots.extend(QuestionSlot("multi", 3.0) for _ in range(10))
    # 判断 10 道 × 1 分
    slots.extend(QuestionSlot("judge", 1.0) for _ in range(10))
    # 计算分析 4 道 × 5 分
    slots.extend(QuestionSlot("calc", 5.0) for _ in range(4))
    # 综合 2 道 × 10 分
    slots.extend(QuestionSlot("comprehensive", 10.0) for _ in range(2))
    return PaperSpec(slots=tuple(slots))


# ---------- 抽样器 ----------


class PaperAssembler:
    """试卷组装器：依赖注入一个 AsyncSession 读取题库。

    流程（每次 start_exam 调用一次）：
      1) 拉取全部 chapters + questions（一次性缓存）
      2) 按 spec.slots 逐槽位选题：
         - 章节加权（章节可用题数）
         - 题型固定（按 slot.type）
         - 难度按 difficulty_target 分配（不足降级）
      3) 缺口 fallback：从全题库抽同类型补足
      4) 至少覆盖 8 章（不足时最后一轮补救）
    """

    def __init__(
        self,
        db: AsyncSession,
        rng: random.Random | None = None,
        spec: PaperSpec | None = None,
    ) -> None:
        """初始化。

        参数:
            db: 异步数据库 session
            rng: 随机数生成器（注入便于测试）
            spec: 试卷规格，默认用 build_default_spec()
        """
        self.db = db
        self.rng = rng or random.Random()
        self.spec = spec or build_default_spec()

    # ----- 公开 API -----

    async def assemble(self) -> list[dict]:
        """组装一份试卷，返回按 sequence 排序的题目列表（字典形式）。

        返回字段：
            sequence, question_id, type, chapter_id, chapter_code,
            difficulty, stem, options (list|None), score
        """
        questions = await self._load_questions()
        chapters = await self._load_chapters()

        # 按 (chapter_id, type, difficulty) 建立索引
        pool = self._build_pool(questions)

        # 按 spec 顺序逐槽位抽题
        picked: list[Question] = []
        picked_ids: set[int] = set()  # 全程维护已选 ID，禁重复
        slot_difficulty_plan = self._plan_difficulty_distribution()
        type_cursor: dict[str, int] = defaultdict(int)

        for slot in self.spec.slots:
            # 章节加权抽样（保护性约束：候选 ≥ 3）
            chapter_id = self._sample_chapter(pool, chapters, slot.type)
            difficulty = slot_difficulty_plan[type_cursor[slot.type]]
            type_cursor[slot.type] += 1

            q = self._pick_from_pool(
                pool,
                chapter_id=chapter_id,
                q_type=slot.type,
                difficulty=difficulty,
                exclude_ids=picked_ids,
            )
            if q is None:
                # 缺口 1：跨章节同题型同难度
                q = self._pick_from_pool(
                    pool, q_type=slot.type, difficulty=difficulty, exclude_ids=picked_ids
                )
            if q is None:
                # 缺口 2：跨难度补足
                q = self._pick_from_pool(
                    pool, q_type=slot.type, exclude_ids=picked_ids
                )
            if q is None:
                # 缺口 3：综合题无候选 → 用 calc 替代（数据集中只有 4 种题型）
                if slot.type == "comprehensive":
                    q = self._pick_from_pool(pool, q_type="calc", exclude_ids=picked_ids)
            if q is None:
                # 缺口 4：题型都无，跨题型兜底
                q = self._pick_any(pool, exclude_ids=picked_ids)
            if q is None:
                raise RuntimeError(f"题库为空，无法生成试卷（slot={slot}）")
            picked.append(q)
            picked_ids.add(q.id)

        # 至少覆盖 min_chapter_coverage 章 — 不足时最后一轮补救
        picked = self._ensure_chapter_coverage(picked, pool, chapters)

        # 组装返回字典
        chapter_code_by_id = {c.id: c.code for c in chapters}
        out: list[dict] = []
        for idx, q in enumerate(picked, start=1):
            options = json.loads(q.options_json) if q.options_json else None
            out.append(
                {
                    "sequence": idx,
                    "question_id": q.id,
                    "type": q.type,
                    "chapter_id": q.chapter_id,
                    "chapter_code": chapter_code_by_id.get(q.chapter_id, ""),
                    "difficulty": q.difficulty,
                    "stem": q.stem,
                    "options": options,
                    "score": self._slot_score_for(idx),
                }
            )
        return out

    # ----- 内部方法 -----

    def _slot_score_for(self, sequence: int) -> float:
        """根据序列号取对应槽位分值（1-indexed）。"""
        return self.spec.slots[sequence - 1].score

    async def _load_chapters(self) -> list[Chapter]:
        """加载所有章节。"""
        result = await self.db.execute(select(Chapter).order_by(Chapter.id))
        return list(result.scalars().all())

    async def _load_questions(self) -> list[Question]:
        """加载所有题目。"""
        result = await self.db.execute(select(Question))
        return list(result.scalars().all())

    def _build_pool(self, questions: list[Question]) -> dict[tuple[int, str, int], list[Question]]:
        """构建 (chapter_id, type, difficulty) → [Question...] 索引。"""
        pool: dict[tuple[int, str, int], list[Question]] = defaultdict(list)
        for q in questions:
            pool[(q.chapter_id, q.type, q.difficulty)].append(q)
        return pool

    def _plan_difficulty_distribution(self) -> list[int]:
        """为每个槽位预分配目标 difficulty。

        按 difficulty_target 比例切分总题数。
        返回长度 = 总题数，元素 ∈ {1,2,3}（1=easy, 2=medium, 3=hard）。
        """
        n = self.spec.total_questions
        target = self.spec.difficulty_target
        # 精确分配（按比例四舍五入，最后一档补差）
        easy_n = round(n * target["easy"])
        hard_n = round(n * target["hard"])
        medium_n = n - easy_n - hard_n
        plan = [1] * easy_n + [2] * medium_n + [3] * hard_n
        # 打乱顺序（按题型分组内打散）— 使用 self.rng 保证可复现
        self.rng.shuffle(plan)
        return plan

    def _sample_chapter(
        self,
        pool: dict[tuple[int, str, int], list[Question]],
        chapters: list[Chapter],
        q_type: str,
    ) -> int:
        """加权抽样章节 ID（章节权重 ∝ 该章节该题型可用题数，保护性约束 ≥ 3）。"""
        # 按章节统计该题型可用题数（任一难度）
        weights: dict[int, int] = {}
        for ch in chapters:
            count = sum(
                len(pool.get((ch.id, q_type, d), [])) for d in (1, 2, 3)
            )
            if count >= 3:  # 保护性约束
                weights[ch.id] = count
        if not weights:
            # 全章节都不足 3 → 取候选最多的
            counts = {
                ch.id: sum(len(pool.get((ch.id, q_type, d), [])) for d in (1, 2, 3))
                for ch in chapters
            }
            if not counts or max(counts.values()) == 0:
                return self.rng.choice([c.id for c in chapters])
            return max(counts.items(), key=lambda kv: kv[1])[0]

        chapter_ids = list(weights.keys())
        ws = [weights[c] for c in chapter_ids]
        return self.rng.choices(chapter_ids, weights=ws, k=1)[0]

    def _pick_from_pool(
        self,
        pool: dict[tuple[int, str, int], list[Question]],
        q_type: str,
        chapter_id: int | None = None,
        difficulty: int | None = None,
        exclude_ids: set[int] | None = None,
    ) -> Question | None:
        """从池中随机抽一题；可按 (chapter_id, type, difficulty) 过滤。"""
        exclude_ids = exclude_ids or set()
        candidates: list[Question] = []
        for (ch_id, t, d), qs in pool.items():
            if t != q_type:
                continue
            if chapter_id is not None and ch_id != chapter_id:
                continue
            if difficulty is not None and d != difficulty:
                continue
            for q in qs:
                if q.id not in exclude_ids:
                    candidates.append(q)
        if not candidates:
            return None
        return self.rng.choice(candidates)

    def _pick_any(
        self,
        pool: dict[tuple[int, str, int], list[Question]],
        exclude_ids: set[int] | None = None,
    ) -> Question | None:
        """全池随机抽一题（兜底）。"""
        exclude_ids = exclude_ids or set()
        all_qs = [q for qs in pool.values() for q in qs if q.id not in exclude_ids]
        if not all_qs:
            return None
        return self.rng.choice(all_qs)

    def _ensure_chapter_coverage(
        self,
        picked: list[Question],
        pool: dict[tuple[int, str, int], list[Question]],
        chapters: list[Chapter],
    ) -> list[Question]:
        """保证至少覆盖 min_chapter_coverage 章。

        策略：找出未被覆盖的章节，随机挑选某题替换为该章节题目（保证题型不变）。
        """
        covered = {q.chapter_id for q in picked}
        if len(covered) >= self.spec.min_chapter_coverage:
            return picked

        missing = [c.id for c in chapters if c.id not in covered]
        picked_ids = {q.id for q in picked}

        for target_chapter in missing:
            if len(covered) >= self.spec.min_chapter_coverage:
                break
            # 找一个能换的题：当前 picked 中某题，去掉后能从 target_chapter 换一道
            replaced = False
            for idx, q in enumerate(picked):
                # 从 target_chapter 找一个同题型题（不限难度）
                candidate = self._pick_from_pool(
                    pool,
                    q_type=q.type,
                    chapter_id=target_chapter,
                    exclude_ids=picked_ids,
                )
                if candidate is None:
                    continue
                # 替换
                picked_ids.discard(q.id)
                picked_ids.add(candidate.id)
                picked[idx] = candidate
                covered.add(target_chapter)
                replaced = True
                break
            if not replaced:
                # 该章节无任何题目 → 跳过
                continue
        return picked


# ---------- 模式化统一入口（fix-22 新增，mixed 实现在 fix-20）----------


async def _mixed_branch(
    assembler: "PaperAssembler",
    deepseek_client: object | None,
) -> list[dict]:
    """混合模式实现:standard 出题 → ~30% 题 AI 改编(防幻觉)。

    Parameters
    ----------
    assembler : PaperAssembler
        已绑定 session 的抽样器(供复用 `_load_questions()`)。
    deepseek_client : DeepSeekClient | None
        改编所用 LLM 客户端。无/未配置 → 直接返回 standard 试卷
        (不静默打 is_adapted 标记,避免误标)。

    Returns
    -------
    list[dict]
        与 `assemble()` 同结构的题目列表。被改编的题会新增字段:
        - `is_adapted`: True
        - `source_question_id`: 原题 id(同 question_id,保留以兼容)
        - `adapted_answer`: 改编后的答案(基于新数值重算)
        - `adapted_key_points`: 同原题 key_points(必须复用,防幻觉)
        - `adapted_analysis`: 改编后 ≤ 100 字解析

    Notes
    -----
    防幻觉护栏全部在 adapt_service.adapt_one_question 内:
    - type 不变 / key_points 完全复用 / 答案数值等价
    失败 fallback → 保留原题(含 None-adapted 字段),**永不瞎编内容**。

    fix-22 P0 性能优化：改编并发执行。
    - 原实现：串行 for-loop，~12 题 × DeepSeek 单次 ~5-8s = 60-96s
    - 新实现：asyncio.gather 并发，DeepSeek API 调用天然异步，
      期望耗时降至 ~10-15s（单请求时间，不叠加）。
    - 单题异常隔离：每个 task 包 try-except，失败返回 None（保留原题 fallback）。
    """
    paper = await assembler.assemble()

    if deepseek_client is None or not getattr(deepseek_client, "configured", False):
        logger.info(
            "mode=mixed: deepseek 不可用 / 未配置 → fallback standard(全部原题)"
        )
        return paper

    # 复用同一 session 加载完整 Question(需 answer/key_points)
    full_questions = await assembler._load_questions()
    full_by_id = {q.id: q for q in full_questions}

    # 选 ~30% 题做改编(max(1, ...) 保证小数试卷也能至少 1 道)
    n_adapt = max(1, int(len(paper) * 0.30))
    candidate_indices = list(range(len(paper)))
    random.shuffle(candidate_indices)

    # 延迟 import:避免 adapt_service → deepseek_client 循环,且便于测试 patch
    from app.services.adapt_service import adapt_one_question

    # 准备候选 idx → original_dict + seeds_dicts
    # 优化：限制候选数 = n_adapt × 2 + buffer(允许 ~50% 失败 fallback 后仍能凑够 n_adapt)
    # ponytail: 之前是 gather 全部 41 题，再过滤前 n_adapt 个 → 浪费大量 LLM 调用。
    # 现在限制候选数 → 减少调用浪费 + 加快总耗时。
    # 设 max_candidates = min(41, n_adapt * 2 + 4) — 4 是经验 buffer(LLM 防幻觉失败 ~20%)
    max_candidates = min(len(paper), n_adapt * 2 + 4)

    candidate_tasks: list[tuple[int, dict, list[dict]]] = []
    for idx in candidate_indices:
        if len(candidate_tasks) >= max_candidates:
            break
        qdict = paper[idx]
        qid = qdict["question_id"]
        qorm = full_by_id.get(qid)
        if qorm is None:
            continue

        original_dict: dict = {
            "id": qorm.id,
            "type": qorm.type,
            "chapter": qdict.get("chapter_code", ""),
            "stem": qorm.stem,
            "options": json.loads(qorm.options_json) if qorm.options_json else None,
            "answer": qorm.answer,
            "key_points": (
                json.loads(qorm.key_points_json) if qorm.key_points_json else []
            ),
            "analysis": qorm.analysis,
        }

        # few-shot:同章节同题型(排除自己),最多 3 道
        seeds_orms = [
            q
            for q in full_questions
            if q.chapter_id == qorm.chapter_id
            and q.type == qorm.type
            and q.id != qorm.id
        ][:3]
        if not seeds_orms:
            # 同章节同题型无邻居 → 跳过(不拿跨章节做种子,避免污染)
            continue
        seeds_dicts = [
            {"id": s.id, "type": s.type, "stem": s.stem, "answer": s.answer}
            for s in seeds_orms
        ]

        candidate_tasks.append((idx, original_dict, seeds_dicts))

    # 限制并发数：避免 DeepSeek rate-limit / 大量并发触发 IP 限流
    # ponytail: 候选数现在 ≤ 28（n_adapt×2+4）；并发 12 对 DeepSeek 默认 tier 是安全的。
    concurrency_limit = min(len(candidate_tasks), 12)
    sem = asyncio.Semaphore(concurrency_limit)

    async def _adapt_one_safe(
        idx: int, original_dict: dict, seeds_dicts: list[dict]
    ) -> tuple[int, dict | None]:
        """单个改编（带 try-except 兜底 + semaphore 限流）。

        返回:
            (idx, adapted_dict | None)
            - 成功 → (idx, adapted)
            - 失败 / 异常 → (idx, None) —— caller 保留原题
        """
        async with sem:
            try:
                adapted = await adapt_one_question(
                    deepseek_client, original_dict, seeds_dicts
                )
                return (idx, adapted)
            except Exception as e:  # noqa: BLE001 - 单题失败必须吞掉
                logger.error(
                    "_mixed_branch 单题改编异常 (idx=%s, qid=%s): %s",
                    idx,
                    original_dict.get("id"),
                    e,
                )
                return (idx, None)

    # 并发跑所有候选 — 但限制有效改编数 ≤ n_adapt
    # gather 全部并发（最多 12 个并发），保留前 n_adapt 个成功的结果
    tasks = [
        _adapt_one_safe(idx, orig, seeds)
        for (idx, orig, seeds) in candidate_tasks
    ]
    results = await asyncio.gather(*tasks)

    # 按 index 顺序应用成功结果；超过 n_adapt 的丢弃
    adapted_count = 0
    for idx, adapted in results:
        if adapted is None:
            # 改编失败 → 保留原题（fallback，永不瞎编）
            continue
        if adapted_count >= n_adapt:
            # 已达到目标改编数 → 剩余的丢弃（保留原题）
            logger.debug("mixed: 已达 n_adapt=%d，跳过 idx=%s", n_adapt, idx)
            continue
        qdict = paper[idx]
        qdict["stem"] = adapted["stem"]
        qdict["options"] = adapted["options"]
        qdict["is_adapted"] = True
        qdict["source_question_id"] = adapted.get("source_question_id") or qdict["question_id"]
        qdict["adapted_answer"] = adapted["answer"]
        qdict["adapted_key_points"] = adapted["key_points"]
        qdict["adapted_analysis"] = adapted.get("analysis")
        adapted_count += 1

    logger.info(
        "mode=mixed: adapted %d/%d 题 (deepseek=%s, 并发=%d)",
        adapted_count,
        n_adapt,
        getattr(deepseek_client, "model", "?"),
        concurrency_limit,
    )
    return paper


async def assemble_paper_async(
    subject: str,
    paper_spec: PaperSpec,
    mode: str = "standard",
    deepseek_client: object | None = None,
) -> list[dict]:
    """统一出题入口 — 按 mode 路由到标准 / 混合实现。

    参数:
        subject: 学科 ID（当前仅 'fin-mgmt'，预留多学科扩展）。
        paper_spec: 试卷规格（章节 / 题型 / 分值 / 时长等）。
        mode: 'standard'（默认）走原 `PaperAssembler.assemble()`；
              'mixed' 走 AI 改编分支（fix-20 实现,~30% 题改编 +
              严格防幻觉护栏 + 失败 fallback 保留原题）。
        deepseek_client: 改编所用的 DeepSeekClient 实例。mixed 模式下,
            未配置 / 传 None 会自动 fallback 到 standard 行为(不打
            is_adapted 标记,避免误标)。

    返回:
        与 `PaperAssembler.assemble()` 一致的题目字典列表（按 sequence 排序）。
        字段：`sequence / question_id / type / chapter_id / chapter_code /
        difficulty / stem / options / score`。mixed 模式下被改编的题额外含
        `is_adapted / source_question_id / adapted_answer /
        adapted_key_points / adapted_analysis`。

    抛出:
        ValueError: 当 mode 取值非法时（防御性 validate_mode 会先回退，
            此处保留抛错以暴露上游 bug）。
    """
    mode = validate_mode(mode)
    # 单 session 复用 — 标准与 mixed 共用同一查询,避免双开 session。
    factory = get_session_factory()
    async with factory() as session:
        rng = random.Random()
        assembler = PaperAssembler(session, rng=rng, spec=paper_spec)
        if mode == "standard":
            return await assembler.assemble()
        # mode == "mixed":真实 AI 改编(防幻觉护栏见 adapt_service)
        return await _mixed_branch(assembler, deepseek_client)