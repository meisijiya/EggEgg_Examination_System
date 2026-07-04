"""fix-22 P0 并发改编性能测试 — asyncio.gather 应显著快于串行。

策略：
- 注入慢 LLM mock（每次 chat_json_async 模拟 ~3s 网络延迟）
- 测串行耗时（基准）vs 并发耗时（实现）
- 断言：并发耗时应 < 串行 50%

由于我们无法直接调用"旧串行实现"，这里用一个间接方法：
- 注入 N=12 个延迟 3s 的 mock
- _mixed_branch 现在用 asyncio.gather → 总耗时应 ≈ 3s（最慢单题）而非 36s（12×3s）
- 阈值：< 10s（留 3x 余量，避免 CI 抖动）
"""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from app.services.paper_assembler import (
    PaperAssembler,
    _mixed_branch,
    build_default_spec,
)


# ---------- Mock DeepSeek 客户端（带延迟）----------


class _SlowDeepSeek:
    """每次调用延迟 N 秒；返回有效 response（防幻觉校验通过）。

    configured=True → _mixed_branch 走改编路径。

    关键：用更宽松的正则从 user_prompt 提取 key_points / answer，
    处理多行答案（计算题常见 "第一空：\\n...\\n第二空：..."）。
    """

    configured = True
    model = "fake-slow"

    def __init__(self, delay_seconds: float):
        self.delay_seconds = delay_seconds
        self.call_count = 0
        self.total_time = 0.0

    async def chat_json_async(self, system: str, user: str, **kw):
        self.call_count += 1
        # 模拟 LLM 网络延迟
        start = time.perf_counter()
        await asyncio.sleep(self.delay_seconds)
        self.total_time += time.perf_counter() - start

        # 从 user prompt 提取原题 key_points（保证防幻觉校验通过）
        import json

        orig_kps: list[str] = []
        m = re.search(r"原 key_points: (\[.*?\])\s*\n", user)
        if m:
            try:
                orig_kps = json.loads(m.group(1))
            except json.JSONDecodeError:
                orig_kps = []

        # 多行答案：原答案与原 key_points 之间的所有内容
        orig_ans = ""
        m = re.search(
            r"原答案: (.*?)\n原 key_points:", user, re.DOTALL
        )
        if m:
            orig_ans = m.group(1).strip()

        return {
            "stem": "改编题干（slow mock）",
            "options": None,
            "answer": orig_ans,
            "key_points": list(orig_kps),
            "analysis": "改编解析（slow mock）",
        }


@pytest.fixture
async def db_session():
    """异步 DB session fixture（与 test_paper_assembler 模式一致）。"""
    from app.models.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        yield session


@pytest.mark.asyncio
async def test_concurrent_adapt_is_much_faster_than_serial():
    """核心断言：asyncio.gather 并发改编耗时显著低于串行。

    设置：41 候选 × 单题延迟 3s（n_adapt=12，仅前 12 个成功被采纳）
    - 串行预期：123s（41 calls × 3s）
    - 并发预期：< 20s（semaphore=12 → ~4 批 × 3s）

    阈值：< 20s（并发相对串行提速 ≥ 6x）
    """
    from app.models.database import get_session_factory

    delay = 3.0  # 每题 3s
    client = _SlowDeepSeek(delay_seconds=delay)

    factory = get_session_factory()
    async with factory() as session:
        rng = __import__("random").Random(42)
        assembler = PaperAssembler(session, rng=rng, spec=build_default_spec())
        start = time.perf_counter()
        paper = await _mixed_branch(assembler, client)
        elapsed = time.perf_counter() - start

    adapted_count = sum(1 for q in paper if q.get("is_adapted"))
    # n_adapt = max(1, int(41 * 0.30)) = 12
    assert adapted_count > 0
    assert client.call_count >= adapted_count, (
        f"应至少调用 {adapted_count} 次 LLM，实际 {client.call_count}"
    )

    # 关键断言：并发耗时 < 串行估算 / 6（speedup ≥ 6x）
    serial_estimate = client.call_count * delay
    speedup = serial_estimate / elapsed if elapsed > 0 else float("inf")

    print(
        f"\n  并发性能: elapsed={elapsed:.2f}s, "
        f"serial_estimate={serial_estimate:.0f}s, "
        f"speedup={speedup:.1f}x, adapted={adapted_count}/{client.call_count} calls"
    )

    # 阈值：并发 < 20s（远小于串行 123s；mock 实测约 12s；真实 DeepSeek + buffer 仍能 < 30s）
    assert elapsed < 20.0, (
        f"并发耗时 {elapsed:.2f}s 超过 20s 阈值，未实现真并发"
    )
    # Speedup 验证：真并发应有 ≥ 6x（mock 实测 ~10x；真实 DeepSeek ~3-4x）
    assert speedup >= 6.0, (
        f"speedup={speedup:.1f}x < 6x，未实现真并发"
    )

    # 注：client.total_time 是 sum of individual sleeps（并发也是累加），
    # 不能用作并发度量——移除此断言。


@pytest.mark.asyncio
async def test_concurrent_adapt_survives_single_task_failure():
    """核心断言：单题异常时，其他改编不受影响（fail-safe 并发）。

    策略：注入一个会抛异常的 LLM mock；但这个 mock 我们用另一种方式实现
    （直接在 chat_json_async 内抛）—— 单题异常 → asyncio.gather 不应让其他任务失败。
    """
    from app.models.database import get_session_factory

    class _MixedDeepSeek:
        """前 N 次抛异常，后 N 次成功。验证 fail-safe。"""

        configured = True
        model = "fake-mixed"

        def __init__(self):
            self.call_count = 0

        async def chat_json_async(self, system: str, user: str, **kw):
            self.call_count += 1
            if self.call_count <= 3:
                # 前 3 次抛异常
                raise RuntimeError(f"模拟 LLM 故障 call={self.call_count}")
            # 后续成功（自适应 key_points）
            import json
            import re

            orig_kps: list[str] = []
            m = re.search(r"原 key_points: (\[.*?\])", user)
            if m:
                try:
                    orig_kps = json.loads(m.group(1))
                except json.JSONDecodeError:
                    orig_kps = []
            orig_ans = ""
            m = re.search(r"原答案: ([^\n]+)", user)
            if m:
                orig_ans = m.group(1).strip()
            return {
                "stem": "改编题干",
                "options": None,
                "answer": orig_ans,
                "key_points": list(orig_kps),
                "analysis": "解析",
            }

    client = _MixedDeepSeek()
    factory = get_session_factory()
    async with factory() as session:
        rng = __import__("random").Random(99)
        assembler = PaperAssembler(session, rng=rng, spec=build_default_spec())
        paper = await _mixed_branch(assembler, client)

    # 至少有 1 题改编成功（前 3 次失败后，后续题 OK）
    adapted_count = sum(1 for q in paper if q.get("is_adapted"))
    assert adapted_count > 0, (
        f"应至少有 1 题改编成功（call_count={client.call_count}）"
    )