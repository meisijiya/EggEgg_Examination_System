"""评语池服务 — 3 场景鼓励语随机 pick + LRU cooldown。

Phase 2-Lane-B 简化版:
- 数据层:data/praise/pool.json(3 scenarios × 7 entries = 21 字符串,手写)
- 场景:unanswered / correct / wrong
- 选条:random.choice + 排除最近 N 次(cooldown=3)
- 单例:全局 _praise,get_praise_service() 懒加载

设计取舍(ponytail):
- 不接 AI / 不接 LLM:评语 = 静态鼓励话语,简单直接
- LRU 是 in-memory dict(_recent),重启清零 — 不持久化(MVP 可接受)
- 不写 per-user 持久化文件 — 重启后历史丢失,下个用户/题目重新随机
- 不线程安全:grader 是 sync 函数,单线程跑,够用
"""
from __future__ import annotations

import json
import random
from pathlib import Path

# 评语池 JSON 路径 — packages/backend/app/services/praise_service.py
# → ../../../../../data/praise/pool.json(5 级向上到项目根)
# 推导:services/ → app/ → backend/ → packages/ → EggEgg_Examination_System/
POOL_FILE = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "data"
    / "praise"
    / "pool.json"
)


class PraiseService:
    """评语池服务 - 3 场景鼓励语随机 pick + 低重复。

    Attributes:
        pool: 加载自 pool.json 的 {scenario: list[str]} 字典
        _recent: per-(session, scenario) 最近 pick 的 LRU 列表
    """

    def __init__(self, pool_file: Path | None = None) -> None:
        """初始化 — 立即 load pool。

        参数:
            pool_file: 可选,自定义 pool 文件路径(便于测试用临时文件)
        """
        self.pool = self._load_pool(pool_file or POOL_FILE)
        # key = "{user_session_id}:{scenario}" → 最近 pick 的字符串列表(LRU)
        self._recent: dict[str, list[str]] = {}

    @staticmethod
    def _load_pool(path: Path) -> dict[str, list[str]]:
        """加载 JSON 评语池。

        抛出:
            FileNotFoundError: pool 文件不存在
            json.JSONDecodeError: JSON 格式错误
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 防御:确保每个 scenario 都是非空字符串列表
        for scenario, entries in data.items():
            if not isinstance(entries, list) or not entries:
                raise ValueError(
                    f"praise pool: scenario={scenario!r} 须为非空 list[str],实际 {type(entries).__name__}"
                )
            for entry in entries:
                if not isinstance(entry, str) or not entry.strip():
                    raise ValueError(
                        f"praise pool: scenario={scenario!r} 含非字符串/空字符串"
                    )
        return data

    def pick(
        self,
        scenario: str,
        user_session_id: str = "default",
        cooldown: int = 3,
    ) -> str:
        """随机 pick 一条评语,避免与最近 cooldown 次重复。

        参数:
            scenario: 'unanswered' / 'correct' / 'wrong'(未知 → fallback 'wrong')
            user_session_id: per-session 历史追踪 key(简单字符串,例:"user" / "admin")
            cooldown: 排除最近 N 次(default 3,与 brief 对齐)

        返回:
            pool 中一条字符串(可能是 reset 后重复的)

        行为:
            - 若全部 entries 都在最近 N 次中 → reset recent 重新选
            - 否则从非 recent 中随机 pick
            - 更新 LRU(只保留最近 cooldown 条)
        """
        if scenario not in self.pool:
            # 未知 scenario → fallback wrong(防御性)
            scenario = "wrong"
        candidates = self.pool[scenario]
        key = f"{user_session_id}:{scenario}"
        recent = self._recent.get(key, [])

        # 排除最近 cooldown 条
        available = [c for c in candidates if c not in recent]
        if not available:
            # 全用过了 → reset recent(允许重新从全 pool 选)
            available = candidates
            recent = []
        chosen = random.choice(available)
        # 更新 LRU(只保留最近 cooldown 条)
        recent.append(chosen)
        self._recent[key] = recent[-cooldown:]
        return chosen

    def reset(self) -> None:
        """清空 LRU 历史(测试 / 重置场景用)。"""
        self._recent.clear()


# ---------- 全局单例(懒加载) ----------


_praise: PraiseService | None = None


def get_praise_service() -> PraiseService:
    """获取全局 PraiseService 单例。

    懒加载:首次调用时构造,后续复用。
    ponytail: 不预设 — 避免 import 阶段触发文件 IO,加快冷启动。
    """
    global _praise
    if _praise is None:
        _praise = PraiseService()
    return _praise
