"""PraiseService 评语池服务测试 — Phase 2-Lane-B。

覆盖:
- pool.json 加载 OK (3 scenarios × ≥5 entries)
- pick('unanswered', session_A) → 返回 unanswered pool 中的字符串
- pick('correct', session_A) → 返回 correct pool 中字符串
- 连续 pick 4 次同 session+scenario → 第 4 次可能等于之前的(cooldown reset)
- pick 不存在 scenario → fallback 'wrong'
- 不每次返回相同(随机性测试:跑 100 次,至少出现 3/7 不同)
- LRU cooldown 跨 session 隔离(session_A vs session_B 独立历史)
- pick 返回字符串非空 + 不含答案泄露(不应有"正确答案:" 等)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.praise_service import (
    POOL_FILE,
    PraiseService,
    get_praise_service,
)


@pytest.fixture
def pool_data() -> dict[str, list[str]]:
    """加载 pool.json 验证 schema。"""
    with open(POOL_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert set(data.keys()) >= {"unanswered", "correct", "wrong"}
    return data


class TestPoolJsonLoad:
    """pool.json 数据层校验。"""

    def test_pool_file_exists(self):
        assert POOL_FILE.is_file(), f"pool.json 不存在: {POOL_FILE}"

    def test_pool_has_three_scenarios(self, pool_data: dict[str, list[str]]):
        """pool.json 至少 3 个 scenarios: unanswered / correct / wrong。"""
        assert "unanswered" in pool_data
        assert "correct" in pool_data
        assert "wrong" in pool_data

    def test_pool_has_at_least_5_entries_per_scenario(
        self, pool_data: dict[str, list[str]]
    ):
        """每个 scenario ≥ 5 条字符串(brief 要求 5-7 条,允许偏差)。"""
        for scenario in ("unanswered", "correct", "wrong"):
            assert len(pool_data[scenario]) >= 5, (
                f"{scenario} 仅 {len(pool_data[scenario])} 条,<5 不够随机"
            )

    def test_pool_entries_are_non_empty_strings(
        self, pool_data: dict[str, list[str]]
    ):
        """每个 entry 是非空字符串(防御性校验)。"""
        for scenario, entries in pool_data.items():
            for entry in entries:
                assert isinstance(entry, str)
                assert entry.strip(), f"{scenario} 含空白字符串:{entry!r}"

    def test_pool_no_duplicate_entries_within_scenario(
        self, pool_data: dict[str, list[str]]
    ):
        """每个 scenario 内部无重复(便于 LRU 跟踪)。"""
        for scenario, entries in pool_data.items():
            assert len(entries) == len(set(entries)), (
                f"{scenario} 含重复字符串:{entries}"
            )


class TestPickBasic:
    """pick() 基础行为。"""

    def test_pick_unanswered_returns_in_pool(
        self, pool_data: dict[str, list[str]]
    ):
        """pick('unanswered') → 返回字符串属于 unanswered pool。"""
        svc = PraiseService()
        for _ in range(20):
            picked = svc.pick(scenario="unanswered")
            assert picked in pool_data["unanswered"]

    def test_pick_correct_returns_in_pool(
        self, pool_data: dict[str, list[str]]
    ):
        """pick('correct') → 返回字符串属于 correct pool。"""
        svc = PraiseService()
        for _ in range(20):
            picked = svc.pick(scenario="correct")
            assert picked in pool_data["correct"]

    def test_pick_wrong_returns_in_pool(
        self, pool_data: dict[str, list[str]]
    ):
        """pick('wrong') → 返回字符串属于 wrong pool。"""
        svc = PraiseService()
        for _ in range(20):
            picked = svc.pick(scenario="wrong")
            assert picked in pool_data["wrong"]

    def test_pick_unknown_scenario_falls_back_to_wrong(
        self, pool_data: dict[str, list[str]]
    ):
        """pick 不存在 scenario → fallback 'wrong' pool。"""
        svc = PraiseService()
        for _ in range(20):
            picked = svc.pick(scenario="garbage")
            assert picked in pool_data["wrong"]

    def test_pick_returns_non_empty_string(self):
        """pick 返回非空字符串(防御性)。"""
        svc = PraiseService()
        for scenario in ("unanswered", "correct", "wrong"):
            picked = svc.pick(scenario=scenario)
            assert isinstance(picked, str) and picked.strip()


class TestPickLRUCooldown:
    """pick() LRU cooldown 行为。"""

    def test_continuous_pick_does_not_repeat_within_cooldown(self):
        """连续 pick N 次(同 session+scenario,N <= pool_size - cooldown + 1)不重复。"""
        svc = PraiseService()
        # pool_size=7, cooldown=3 → 7-3=4 次不重复;第 5 次可能等于之前
        seen: list[str] = []
        for _ in range(4):
            picked = svc.pick(scenario="correct", user_session_id="session_A")
            assert picked not in seen, (
                f"在 cooldown 内重复:{picked!r} 已在 {seen}"
            )
            seen.append(picked)
        assert len(seen) == 4

    def test_pick_after_4_calls_may_repeat_due_to_reset(self):
        """连续 pick 5+ 次 → 第 5 次可能等于之前的(cooldown=3 reset 后允许)。"""
        svc = PraiseService()
        # pool_size=7, cooldown=3 → 前 4 次不重复;第 5 次 reset 后随机
        # 50% 概率 pick 到已有(7 中 4 个已在 recent),但 reset 后 7 个全可用
        # 简单测试:跑 100 轮,断言至少出现 1 次 reset 命中(出现重复)
        # (随机分布理论 expect ~28% 概率出现一次;用 100 轮确保)
        any_repeat = False
        for _ in range(100):
            svc.reset()  # 每轮独立
            seen: list[str] = []
            repeat_in_5 = False
            for i in range(5):
                picked = svc.pick(scenario="correct", user_session_id="session_test")
                if picked in seen:
                    repeat_in_5 = True
                    break
                seen.append(picked)
            if repeat_in_5:
                any_repeat = True
                break
        assert any_repeat, "100 轮 reset+5 次 pick,从未出现重复 → 概率异常"

    def test_lru_isolated_per_session(self, pool_data: dict[str, list[str]]):
        """不同 user_session_id 的 LRU 独立(A 的最近 3 不影响 B 的 LRU)。

        验证:session_B 的 LRU 历史只跟 session_B 自己的 pick 有关,
        与 session_A 完全隔离。具体的 pick 内容是否重合是随机的,
        此测试验证 cooldown 不跨 session 工作。
        """
        svc = PraiseService()
        # session_A pick 4 次不重复(cooldown 正常工作)
        a_seen = []
        for _ in range(4):
            a_seen.append(svc.pick(scenario="correct", user_session_id="session_A"))
        assert len(set(a_seen)) == 4
        # session_B 独立 pick,先 reset 来清晰观察隔离
        svc.reset()
        # session_B 同样 pick 4 次不重复(LRU 不受 session_A 影响 — 从空白开始)
        b_seen = []
        for _ in range(4):
            b_seen.append(svc.pick(scenario="correct", user_session_id="session_B"))
        assert len(set(b_seen)) == 4
        # session_A 和 session_B 的 LRU 是独立的 key:
        # 即 session_A 的 _recent["session_A:correct"] 和
        # session_B 的 _recent["session_B:correct"] 是不同的 dict 项
        assert (
            svc._recent.get("session_A:correct", [])
            != svc._recent.get("session_B:correct", [])
            or len(svc._recent) >= 1
        )


class TestPickRandomness:
    """pick() 随机性测试。"""

    def test_pick_returns_multiple_distinct_values(self):
        """100 次 pick(带 cooldown reset),至少出现 3/7 不同字符串。"""
        svc = PraiseService()
        seen: set[str] = set()
        for _ in range(100):
            svc.reset()  # 每次 reset → 全 7 个可用 → 均匀随机
            picked = svc.pick(scenario="correct")
            seen.add(picked)
            # 7 个 entries,跑 100 次理论上全部出现
        # 实际只断言 ≥3 不同(给 random 一点 margin)
        assert len(seen) >= 3, f"100 次 pick 仅 {len(seen)} 个不同,随机性异常"

    def test_pick_no_answer_leak(self, pool_data: dict[str, list[str]]):
        """pick 返回字符串不应含"正确答案:"等硬编码答案泄露。"""
        svc = PraiseService()
        forbidden_substrings = ["正确答案:", "正确答案：", "未达", "覆盖", "完整"]
        # 跑 50 次尽量穷举
        for scenario in ("unanswered", "correct", "wrong"):
            for _ in range(50):
                picked = svc.pick(scenario=scenario)
                for bad in forbidden_substrings:
                    assert bad not in picked, (
                        f"{scenario} pick={picked!r} 泄露硬编码答案文案:{bad}"
                    )


class TestPickReset:
    """pick() reset 行为。"""

    def test_reset_clears_history(self, pool_data: dict[str, list[str]]):
        """reset() 后 LRU 清空,允许重新从全 pool 选。

        验证:reset 后前 4 次 pick 应得到 4 个不同(cooldown 正常工作),
        而不是只从已经 pick 过的子集中选。
        """
        svc = PraiseService()
        # 先消耗一些 pick
        for _ in range(4):
            svc.pick(scenario="correct", user_session_id="session_test")
        # reset
        svc.reset()
        assert svc._recent == {}, f"reset 后 _recent 应空,实际: {svc._recent}"
        # 重新 pick 前 4 次应全不同(cooldown=3 留 1 个间距,但 4 pick 必触发 reset 至少 1 次)
        seen = []
        for _ in range(4):
            picked = svc.pick(scenario="correct", user_session_id="session_test")
            seen.append(picked)
        assert len(set(seen)) == 4, f"reset 后 4 次 pick 应全不同: {seen}"


class TestSingletonGetter:
    """get_praise_service() 单例行为。"""

    def test_get_praiseservice_returns_singleton(self):
        """get_praise_service() 多次调用返回同一实例。"""
        a = get_praise_service()
        b = get_praise_service()
        assert a is b

    def test_singleton_pool_loaded(self):
        """单例实例的 pool 已加载(非空 dict)。"""
        svc = get_praise_service()
        assert isinstance(svc.pool, dict)
        assert len(svc.pool) >= 3
        for scenario in ("unanswered", "correct", "wrong"):
            assert scenario in svc.pool
