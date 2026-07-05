"""评语池服务 — 3 场景鼓励语随机 pick + LRU cooldown。

Phase 2-Lane-B 简化版:
- 数据层:data/praise/pool.json(3 scenarios × 7 entries = 21 字符串,手写)
- 场景:unanswered / correct / wrong
- 选条:random.choice + 排除最近 N 次(cooldown=3)
- 单例:全局 _praise,get_praise_service() 懒加载

Phase 5 fix-5 改造:
- docker 部署时,Path(__file__)/5/.. 解析为 "/"  →  原 POOL_FILE 指向
  /data/praise/pool.json(docker 容器内无此目录)→ grade_answer → get_praise_service()
  → _load_pool 抛 FileNotFoundError → 500
- 修复:_resolve_pool 多路径候选 + 内置 _FALLBACK_POOL 兜底
  候选顺序:custom_path → POOL_FILE(host/dev) → /app/data/praise/pool.json
  (docker-compose mount ../data:/app/data) → builtin
- 设计取舍:不依赖 Dockerfile / docker-compose.yml 改动
  (Phase 5 fix-1/2 已固化);仅 service 内部 graceful fallback

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
# Phase 5 fix-5 注:docker 容器里 __file__=/app/app/services/praise_service.py,
# 5 级 parent 解析到 "/"(因为 /app 只有 2 级深),所以该路径指向
# /data/praise/pool.json — 通常不存在。_resolve_pool 已加 fallback。
POOL_FILE = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "data"
    / "praise"
    / "pool.json"
)

# docker-compose 部署 mount ../data → /app/data;pool.json 在容器内
# 实际可达路径。仅在 host (5 级 parent 解析到项目根) 失败时兜底。
DOCKER_MOUNT_POOL_FILE = Path("/app/data/praise/pool.json")

# Phase 5 fix-5:内置兜底 pool — 任意 IO 全失败时仍能 keep service alive。
# 选自原 pool.json(同样 7 条 × 3 scenario),保证语义一致。
_FALLBACK_POOL: dict[str, list[str]] = {
    "unanswered": [
        "题海无穷, 慢慢来, 每一道都是学习的机会",
        "时间虽紧, 知识常在, 下次一定更稳",
        "未作答是常态, 重要的是从错题中积累",
        "勇敢迈出第一步, 答错了也是成长",
        "每次考试都是宝贵的练习, 加油",
        "遗漏不可怕, 复盘后下次更出色",
        "别气馁, 答完已是胜利, 答案藏在解析里",
    ],
    "correct": [
        "完全正确!概念掌握得很扎实",
        "答对了!这说明你已经理解这个考点",
        "厉害!这道题被你稳稳拿下",
        "答案精准,继续保持这股劲头",
        "熟练掌握,你的努力没有白费",
        "正确!这正是期望的答案",
        "思路清晰,逻辑严密,给你点赞",
    ],
    "wrong": [
        "别灰心,这道题确实有难度,答案解析可以帮你梳理思路",
        "看似接近了,差一点点,再对比正确答案看看差距",
        "错误是成长的一部分,重点是理解背后的逻辑",
        "再仔细看看题目要求,下次会更好",
        "这一步没走通,回顾一下相关章节的知识点",
        "暂时未通过没关系,这正是补强的好机会",
        "别放弃,错的题都是进步的台阶",
    ],
}


def _validate_pool_dict(data: dict) -> dict[str, list[str]]:
    """校验 pool dict 格式 — 每个 scenario 是非空字符串列表。

    Phase 5 fix-5 抽取为独立函数,便于 _resolve_pool 复用。
    ponytail: 防御性 — 防止 pickle/regex 异常格式污染 grader。

    参数:
        data: 解析后的 JSON dict

    返回:
        验证通过的 data 本身(原引用返回)

    抛出:
        ValueError: 格式不合法
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"praise pool: 须为 dict[str, list[str]],实际 {type(data).__name__}"
        )
    for scenario, entries in data.items():
        if not isinstance(entries, list) or not entries:
            raise ValueError(
                f"praise pool: scenario={scenario!r} 须为非空 list[str],"
                f"实际 {type(entries).__name__}"
            )
        for entry in entries:
            if not isinstance(entry, str) or not entry.strip():
                raise ValueError(
                    f"praise pool: scenario={scenario!r} 含非字符串/空字符串"
                )
    return data


class PraiseService:
    """评语池服务 - 3 场景鼓励语随机 pick + 低重复。

    Attributes:
        pool: 加载自 pool.json(或 _FALLBACK_POOL)的 {scenario: list[str]} 字典
        pool_source: 实际加载来源标识 — 'file:<path>' | 'fallback_builtin'
        _recent: per-(session, scenario) 最近 pick 的 LRU 列表
    """

    def __init__(self, pool_file: Path | None = None) -> None:
        """初始化 — 多路径候选 + 内置 fallback。

        Phase 5 fix-5:docker 部署时原 POOL_FILE(/data/praise/pool.json)不存在,
        现支持 _resolve_pool 候选列表,最后 fallback 到 _FALLBACK_POOL。

        参数:
            pool_file: 可选,自定义 pool 文件路径(便于测试用临时文件)
        """
        self.pool, self.pool_source = self._resolve_pool(pool_file)
        # key = "{user_session_id}:{scenario}" → 最近 pick 的字符串列表(LRU)
        self._recent: dict[str, list[str]] = {}

    @staticmethod
    def _resolve_pool(custom_path: Path | None) -> tuple[dict[str, list[str]], str]:
        """多候选路径解析 pool — 末位 builtin fallback。

        Phase 5 fix-5:docker 部署暴露了 hard-code 路径在容器内缺失。
        候选顺序:
        1. custom_path(显式传入,测试用)— 文件存在则用
        2. POOL_FILE(host dev:__file__ → 项目根/data/praise/pool.json)
        3. DOCKER_MOUNT_POOL_FILE(/app/data/praise/pool.json,docker mount)
        4. _FALLBACK_POOL(内置 — 任意 IO 失败时仍可 pick)

        参数:
            custom_path: 可选,显式指定 pool 文件

        返回:
            (pool_dict, source_str) — pool 字典 + 来源标识('file:<path>' | 'fallback_builtin')
        """
        # 候选路径(顺序敏感)
        candidates: list[Path] = []
        if custom_path is not None:
            candidates.append(custom_path)
        candidates.append(POOL_FILE)
        candidates.append(DOCKER_MOUNT_POOL_FILE)

        for path in candidates:
            try:
                if path.is_file():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # 验证 schema,失败则继续下一个候选
                    try:
                        _validate_pool_dict(data)
                    except ValueError:
                        continue
                    return data, f"file:{path}"
            except (OSError, json.JSONDecodeError):
                # IO/JSON 错误 → 静默 fallback 到下一个候选
                continue

        # 所有候选失败 → builtin(pool file 完全不可用时仍能跑 grader)
        # 用 copy() 避免共享同一份 module-level dict 引用(防御性)
        return {k: list(v) for k, v in _FALLBACK_POOL.items()}, "fallback_builtin"

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
