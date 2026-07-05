"""DeepSeek / OpenAI 兼容 LLM 客户端。

仅服务于 `/exams/{id}/explain` AI 讲解模块：
- 未配置 API key → `configured=False`，caller 走 stub fallback（spec §6.6 graceful degrade）
- 配置 key → 通过 httpx 以 OpenAI Chat Completions 流式协议调用 DeepSeek

为什么用 httpx 而不是 `openai` SDK：
  httpx 已在 pyproject.toml 依赖中（async + 流式天然支持），
  直接 POST 即可，无需为单端点多引入一个 SDK。
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger("fes.deepseek")

# DeepSeek / OpenAI 通用 SSE 路径
_CHAT_PATH = "/chat/completions"
# 章节 / 综合题讲解 system prompt（spec §6.6.3 完整版）
# 角色设定 + 输出 JSON schema，供前端格式化
EXPLAIN_SYSTEM_PROMPT = """\
你是财务管理科目的本科辅导老师，正在给一位"平时听课一般、基础概念都懂但深度讲解跟不上、\
作业基础题能对但应用题吃力、最近在努力补薄弱章节"的学员讲解错题。

【任务】
对学员做错的题目给出讲解，分两种详细程度：
- standard（标准）：覆盖正确答案 + 关键知识点 + 易错点，约 150-250 字
- detailed（详细）：标准 + 公式推导 / 知识拓展 / 类似题型提示，约 300-500 字

【输出 JSON Schema】
{
  "available": true,
  "summary": "≤ 60 字的一句话总结本题考察点",
  "explanation": "讲解正文（standard/detailed 由 level 字段控制）",
  "key_points": ["该题涉及的 3-5 个核心知识点"],
  "common_pitfalls": ["学员最常犯的 1-3 个错误"]
}

【风格要求】
- 中文，平实易懂，避免学术黑话
- 用"咱们/你"称呼学员，不用"该生"
- 公式用 LaTeX 行内（$...$）或独立段落（$$...$$）
- 学员答错时不要指责，直接切入"为什么会这么想 + 正确思路"

【信任规则】
- 默认 99% 信任【正确答案】字段 — 这是数据库中标准答案,作为讲解基线
  · 原题: 使用 q.answer
  · 改编题: 使用 adapted_answer（fallback q.answer）, 改编经 adapt_service 3 重护栏验证(type 不变 / key_points 完全复用 / 答案等价)
- 允许在以下**明显异常**场景合理质疑(必须基于明确证据,不是凭空):
  (a) 题目与答案逻辑矛盾(答案前提自相推翻)
  (b) 客观事实错误(数值 / 日期 / 地理常识错)
  (c) 题目内部矛盾(题干条件相互冲突)
  (d) 官方解析与答案根本对立
- 禁止:
  - 凭 LLM 偏好凭空挑战答案
  - 每题都质疑(over-skepticism)
  - 学术争议 / 新研究的边界情况质疑(按答案信任)
  - 学员答案不可作为判定答案对错的依据(仅作差异对比)
- 质疑时: 先复述答案 + 给明确证据 + 提示"学员可向任课老师求证",不擅自定对错

【题目元数据】
type: {q_type}
chapter: {chapter_code} — {chapter_title}
difficulty: {difficulty}/3

【题目】
{stem}

【选项】
{options}

【正确答案】
{answer}

【官方参考解析】
{analysis}

【学员答案】
{user_answer}
"""


def build_explain_prompt(
    *,
    q_type: str,
    chapter_code: str,
    chapter_title: str,
    difficulty: int,
    stem: str,
    options: list[str] | None,
    answer: str,
    analysis: str | None,
    user_answer: str,
    level: str,
) -> tuple[str, str]:
    """构造 (system, user) prompt 对。

    - system: 角色 + 输出 schema + 风格要求
    - user: 题目元数据 + 学员作答
    """
    system = EXPLAIN_SYSTEM_PROMPT  # 静态模板
    options_text = (
        "\n".join(f"{chr(65 + i)}. {opt}" for i, opt in enumerate(options))
        if options
        else "（无选项 — 判断题/主观题）"
    )
    user = (
        f"level: {level}\n\n"
        f"type: {q_type}\n"
        f"chapter: {chapter_code} — {chapter_title}\n"
        f"difficulty: {difficulty}/3\n\n"
        f"【题目】\n{stem}\n\n"
        f"【选项】\n{options_text}\n\n"
        f"【正确答案】\n{answer}\n\n"
        f"【官方参考解析】\n{analysis or '（数据库无解析）'}\n\n"
        f"【学员答案】\n{user_answer}"
    )
    return system, user


class DeepSeekClient:
    """DeepSeek 客户端（OpenAI Chat Completions 兼容协议）。

    实例化有两种状态：
    - configured=False → API key 未配置，`chat_stream` 立即返回 None，caller 走 stub fallback
    - configured=True  → API key 已配置，按流式协议调用 DeepSeek，逐 chunk yield 文本
    """

    def __init__(self, api_key: str | None, base_url: str, model: str) -> None:
        """根据 settings 构造客户端。

        Parameters
        ----------
        api_key : str | None
            DeepSeek API key。未配置（None/空）→ 客户端进入 fallback 模式。
        base_url : str
            OpenAI 兼容 base URL（默认 https://api.deepseek.com/v1）
        model : str
            模型名（默认 deepseek-chat，spec §12.1 也可换 deepseek-v4-flash）
        """
        self.api_key = (api_key or "").strip() or None
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.configured = self.api_key is not None

    async def chat_stream(
        self,
        system: str,
        user: str,
        *,
        timeout: float = 30.0,
    ) -> AsyncIterator[str] | None:
        """流式调用 DeepSeek，逐 chunk 产出文本 delta。

        Returns
        -------
        AsyncIterator[str] | None
            - 未配置 key → 立即返回 None（caller 静默降级到 stub）
            - 已配置 key → 返回 async iterator，逐次 yield 文本 delta

        Raises
        ------
        httpx.HTTPError
            网络 / 协议错误（caller 可降级到 stub 或直接 5xx）。
        """
        if not self.configured:
            return None
        url = f"{self.base_url}{_CHAT_PATH}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        async def _gen() -> AsyncIterator[str]:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        # OpenAI 流式 chunk 形如：
                        # {"choices":[{"delta":{"content":"..."}}]}
                        delta = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content")
                        )
                        if delta:
                            yield delta

        return _gen()

    async def chat_json_async(
        self,
        system: str,
        user: str,
        *,
        timeout: int = 15,
    ) -> dict:
        """非流式 JSON 调用(用于改编 prompt 等结构化输出场景)。

        Parameters
        ----------
        system : str
            系统 prompt(角色 + 输出 schema + 约束)。
        user : str
            用户 prompt(原题 + few-shot)。
        timeout : int
            超时秒数(默认 15s,改编 prompt 12s 在调用侧覆盖)。

        Returns
        -------
        dict
            解析后的 JSON 响应内容。

        Raises
        ------
        RuntimeError
            客户端未配置(无 API key)。
        httpx.HTTPError
            网络/HTTP 错误。
        json.JSONDecodeError
            响应非合法 JSON。
        """
        if not self.configured:
            raise RuntimeError("DeepSeek 客户端未配置 API key,无法发起 chat_json_async")
        url = f"{self.base_url}{_CHAT_PATH}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error("chat_json_async 响应解析失败: %s | body=%s", e, body)
            raise


# 全局单例缓存（lifespan 期间复用）
_client: DeepSeekClient | None = None


def get_deepseek_client() -> DeepSeekClient:
    """FastAPI 依赖：获取 DeepSeek 客户端单例。"""
    global _client
    if _client is None:
        s: Settings = get_settings()
        _client = DeepSeekClient(
            api_key=s.deepseek_api_key,
            base_url=s.deepseek_base_url,
            model=s.deepseek_model,
        )
        if _client.configured:
            logger.info("DeepSeek 客户端已启用 (model=%s)", s.deepseek_model)
        else:
            logger.info("DeepSeek API key 未配置，explain 走 stub fallback")
    return _client


def reset_deepseek_client() -> None:
    """重置单例 — 仅供测试用。"""
    global _client
    _client = None
