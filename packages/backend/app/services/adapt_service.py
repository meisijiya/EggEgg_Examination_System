"""AI 改编题服务 — 严格防幻觉。

改编约束（spec v6 §6.4 改编 prompt 升级版）：
- 99% 信任预处理数据,**严禁**重新解释财务概念
- 改编只能改:数值(金额/比率/年份)/ 场景主体(公司名/项目名)/ 选项顺序
- **严禁**改变:题型、答案正确性、章节归属、key_points
- **严禁**编造新概念、新公式、新法规
- 输出必须严格 JSON(schema 不匹配 → 整题重抽,最多 2 次)
- 失败 fallback 保留原题(永不存进 DB 瞎编内容)
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------- 防幻觉 System Prompt(spec v6 §6.4 改编 prompt 升级版)----------

ADAPT_SYSTEM_PROMPT = """你是财务管理科目备考出题助手,专门做"基于原题的少量改编"。

【数据可信度约定 — 极其重要】
本任务基于已经 Agent 团队多轮审查 + 用户人工全量 review 的预处理原题数据。
请 99% 信任原题答案、解析、key_points。
**严禁**:
- 重新解释财务概念或质疑原题答案
- 改变题目的正确答案
- 编造新的财务概念、公式、法规
- 改变章节归属

【改编约束 — 严格遵循】
改编**只能**改以下内容:
1. **数值**:金额(100 万 → 200 万)/ 比率(6% → 8%)/ 年限(5 年 → 3 年)
2. **场景主体**:公司名(甲公司 → 乙公司)/ 项目名(设备 A → 设备 B)/ 投资人(A → B)
3. **选项顺序**:可重新排列但**答案不能变**
4. **计算结果同步更新**:如果原题答案依赖某数值,改编后答案也要同步用新数值重算

**严禁**改的:
- 题型(单选/多选/判断/计算/综合)
- 答案正确性
- key_points(必须复用原题 key_points)
- 章节归属
- 解析(解析必须仍然覆盖同一考点,≤ 100 字)

【输出 JSON Schema — 严格匹配】
{
  "stem": "<改编后题干,保持考点和结构>",
  "options": ["A...", "B...", "C...", "D..."] | null,
  "answer": "<标准答案,与原题答案等价(仅数值/场景变)>",
  "key_points": ["<原题 key_points,原样保留>"],
  "analysis": "<≤ 100 字解析,仍然针对同一考点>"
}

【Few-shot 示例】
原题 #123:甲公司发行 5 年期债券 1000 万,名义利率 6%,求终值。
改编后:甲公司发行 5 年期债券 2000 万,名义利率 6%,求终值。
(数值 1000→2000,其他不变,答案按 2000 重算 = 2000 × F/P(6%,5))

请基于下列原题做改编。"""


# ---------- 校验函数 ----------


def _answers_equivalent(a: str, b: str) -> bool:
    """判断两个答案是否等价(允许数值差异但考点相同)。

    规则(顺序敏感):
    - 完全相等 → True
    - 空串任意一方 → False
    - 双方均为纯字母选项(单选/多选答案,如 "A"/"AB")→ 严格相等,否则 False
      (此判在前,避免无数字时被 nums_a == nums_b 误判)
    - 提取数字部分排序后相等 → True(数值题容差)
    - 其他 → False(防止文字答案飘移)
    """
    if a == b:
        return True
    if not a or not b:
        return False
    # 纯字母选项答案(单选/多选)→ 必须完全相等
    if re.match(r"^[A-Z]+$", a) and re.match(r"^[A-Z]+$", b):
        return a == b
    nums_a = sorted(re.findall(r"\d+\.?\d*", a))
    nums_b = sorted(re.findall(r"\d+\.?\d*", b))
    if nums_a and nums_a == nums_b:  # 双方必须有数字才认
        return True
    return False


def _extract_numbers(s: str) -> list[str]:
    """从文本中提取全部数字(用于测试 + 等价性 debug)。"""
    return re.findall(r"\d+\.?\d*", s)


# ---------- 主入口 ----------


async def adapt_one_question(
    client: Any,
    original_question: dict[str, Any],
    seeds: list[dict[str, Any]],
    *,
    chat_json_fn: Optional[Callable[..., Awaitable[dict]]] = None,
    max_attempts: int = 2,
) -> Optional[dict[str, Any]]:
    """改编一道题(基于原题 + few-shot),失败返回 None → caller fallback 保留原题。

    Parameters
    ----------
    client : Any
        DeepSeekClient 实例(或任意拥有 chat_json_async 的对象)。
    original_question : dict
        至少包含 id / type / chapter / stem / options / answer / key_points / analysis。
    seeds : list[dict]
        同章节同题型原题参考(seed 用作 few-shot),每条需要 id / stem / answer。
    chat_json_fn : callable, optional
        自定义 chat 函数(默认用 client.chat_json_async)。用于测试时注入 mock。
    max_attempts : int
        最大重试次数(schema 不匹配 / 校验失败时整题重抽)。

    Returns
    -------
    dict | None
        成功 → 返回改编后题(包含 is_adapted=True / source_question_id);
        失败 → 返回 None,调用方应保留原题。
    """
    chat_fn = chat_json_fn or getattr(client, "chat_json_async", None)
    if chat_fn is None:
        logger.error("adapt_one_question: client 无 chat_json_async 方法")
        return None

    # Few-shot 原文片段
    few_shot = "\n".join(
        f"原题 #{s.get('id', '?')}: {s.get('stem', '')}\n"
        f"答案: {s.get('answer', '')}"
        for s in (seeds or [])[:3]
    )

    user_prompt = (
        f"【原题】\n"
        f"ID: {original_question.get('id', '?')}\n"
        f"题型: {original_question.get('type', '')}\n"
        f"章节: {original_question.get('chapter', '')}\n"
        f"题干: {original_question.get('stem', '')}\n"
        f"选项: {json.dumps(original_question.get('options', []), ensure_ascii=False)}\n"
        f"原答案: {original_question.get('answer', '')}\n"
        f"原 key_points: {json.dumps(original_question.get('key_points', []), ensure_ascii=False)}\n\n"
        f"【Few-shot 同章节同题型原题参考】\n"
        f"{few_shot}\n\n"
        f"请严格按 system prompt 约束输出改编后的 JSON。\n"
    )

    last_err: Exception | None = None

    for attempt in range(max_attempts):
        try:
            response = await chat_fn(system=ADAPT_SYSTEM_PROMPT, user=user_prompt, timeout=12)
            if not isinstance(response, dict):
                logger.warning(
                    "改编响应非 dict (qid=%s, attempt=%s): %r",
                    original_question.get("id"),
                    attempt,
                    response,
                )
                continue

            # 校验 1:type 不变
            if response.get("type") and response["type"] != original_question.get("type"):
                logger.warning(
                    "改编 type 变化 (qid=%s): 跳过",
                    original_question.get("id"),
                )
                continue

            # 校验 2:key_points 必须完全复用原题(spec v6 §6.4 强约束)
            if set(response.get("key_points", [])) != set(original_question.get("key_points", [])):
                logger.warning(
                    "改编 key_points 变化 (qid=%s): 跳过",
                    original_question.get("id"),
                )
                continue

            # 校验 3:答案数值/字母等价
            new_ans = response.get("answer", "")
            orig_ans = original_question.get("answer", "")
            if not _answers_equivalent(new_ans, orig_ans):
                logger.warning(
                    "改编答案不等价 (qid=%s): '%s' vs '%s'",
                    original_question.get("id"),
                    new_ans,
                    orig_ans,
                )
                continue

            # 校验通过
            return {
                "id": original_question["id"],
                "type": original_question["type"],
                "chapter": original_question.get("chapter", ""),
                "stem": response.get("stem") or original_question.get("stem", ""),
                "options": response.get("options", original_question.get("options")),
                "answer": new_ans,
                "key_points": list(original_question.get("key_points", [])),
                "analysis": response.get("analysis", original_question.get("analysis")),
                "is_adapted": True,
                "source_question_id": original_question.get("id"),
            }
        except Exception as e:  # pragma: no cover - 网络/解析异常
            last_err = e
            logger.error(
                "改编失败 (qid=%s, attempt=%s): %s",
                original_question.get("id"),
                attempt,
                e,
            )

    if last_err:
        logger.warning(
            "adapt_one_question 全部 %d 次失败 (qid=%s): %s",
            max_attempts,
            original_question.get("id"),
            last_err,
        )
    return None
