"""Docker 配置回归测试 — 验证 deploy/ 配置符合用户决策 + spec。

覆盖:
1. Dockerfile build-time 数据 COPY
2. docker-compose.yml VOLUME /app/data 暴露 + 限制 + labels + name
3. .env.example 必填字段
4. .gitignore 排除 secrets / coverage / runtime DB
5. 3 docs 相对路径解析正确

不依赖 docker daemon — 只 inspect config 文件内容。

执行(项目根):
    cd packages/backend && uv run pytest deploy/tests/test_docker_config.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]  # packages/backend/tests → repo root
DEPLOY_DIR = REPO_ROOT / "deploy"


def test_dockerfile_has_data_copy() -> None:
    """Verify build-time data COPY exists in deploy/Dockerfile Stage 2 (用户决策: 双层策略)。"""
    dockerfile = (DEPLOY_DIR / "Dockerfile").read_text()
    assert "COPY data/final/finance.db" in dockerfile, (
        "build-time COPY data/final/finance.db 缺失 — 无法保证全新拉起即有题库"
    )
    assert "/app/data/final/finance.db" in dockerfile, (
        "COPY 目标路径 /app/data/final/finance.db 缺失 — finance.db 应入 /app/data"
    )


def test_compose_has_data_volume_and_labels() -> None:
    """Verify docker-compose.yml VOLUME /app/data 暴露 + labels + 显式 name + 资源限制。"""
    compose_text = (DEPLOY_DIR / "docker-compose.yml").read_text()
    compose = yaml.safe_load(compose_text)

    services = compose.get("services", {})
    assert services, "services 块缺失"
    service_name = next(iter(services))
    svc = services[service_name]

    # 1. 双层 VOLUME 挂载
    volumes = svc.get("volumes", [])
    assert any("/app/data" in str(v) for v in volumes), (
        f"VOLUME /app/data 缺失 — 双层策略要求挂载点能覆盖 build-time COPY\n实际: {volumes}"
    )

    # 2. labels 必填 3 项 (version / data-baked / runtime-mount)
    labels = svc.get("labels", [])
    label_str = " ".join(str(l) for l in labels)
    assert "com.egg-egg-exam-system.version" in label_str, "version label 缺失"
    assert "com.egg-egg-exam-system.data-baked" in label_str, "data-baked label 缺失"
    assert "com.egg-egg-exam-system.runtime-mount" in label_str, "runtime-mount label 缺失"

    # 3. 显式 name (compose v2 项目名)
    assert "name" in compose, "compose 顶层 name 缺失 — 无法用确定性项目名"

    # 4. 资源限制保留
    deploy = svc.get("deploy", {}).get("resources", {})
    limits = deploy.get("limits", {})
    assert limits.get("memory") == "768M", f"mem limit 应为 768M, 实际: {limits}"
    assert limits.get("cpus") == "0.8", f"cpu limit 应为 0.8, 实际: {limits}"


def test_env_example_required_keys() -> None:
    """Verify .env.example has all required keys (用户决策: 必填 + optional)。"""
    env_content = (REPO_ROOT / ".env.example").read_text()
    required = [
        "JWT_SECRET",
        "USER_PASSWORD",
        "ADMIN_PASSWORD",
        "CORS_ORIGINS",
        "DEEPSEEK_API_KEY",  # optional, fallback 到本地解析
    ]
    missing = [k for k in required if k not in env_content]
    assert not missing, f".env.example 缺字段: {missing}"


def test_gitignore_excludes_secrets_and_runtime() -> None:
    """Verify .gitignore 排除 secrets / coverage / runtime SQLite。

    语义覆盖 (不是字面匹配):
    - .env* 全部(allowlist negate .env.example)
    - *.coverage / *,cover / coverage.json(pycoverage 三种产物)
    - data/final/*.db(运行时重生成的题库)
    """
    gi = (REPO_ROOT / ".gitignore").read_text()

    # Secrets
    assert re.search(r"^\.env\b|^\\.env\\*?$", gi, re.MULTILINE), ".env 排除缺失"
    assert ".env.*" in gi, ".env.* 排除缺失"

    # Coverage — pycoverage 三种产物: 单文件 / 子目录 / .coverage.{env} / *,cover backup
    coverage_patterns = ["*.coverage", "*,cover", "coverage.json"]
    miss_cov = [p for p in coverage_patterns if p not in gi]
    assert not miss_cov, f"coverage 排除缺失: {miss_cov}"

    # Runtime DB
    assert "data/final/" in gi or "data/final/*.db" in gi, (
        "data/final/*.db 排除缺失 — 题库不入 git"
    )


def test_docs_paths_resolve() -> None:
    """Verify AGENTS.md / SUBJECT_ONBOARDING.md / OPERATIONS.md 引用的相对路径全部解析正确。

    覆盖 6 处 broken paths 的回归 (fix-30 Phase 2-final #4):
    - AGENTS.md: spec 路径
    - SUBJECT_ONBOARDING.md: ../../packages → ../packages (5 处)
    - OPERATIONS.md: ../README.md / ../spec 路径

    只验证 relative 路径(`./` `../` 或纯文件名), 忽略 http/file-absolute/anchor。
    """
    docs = [
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "docs" / "SUBJECT_ONBOARDING.md",
        REPO_ROOT / "deploy" / "OPERATIONS.md",
    ]
    # 相对路径(以 ./ ../ 或文件名开头, 不是 http/file:/锚点)
    rel_link_re = re.compile(r"\]\(([^()#]+)\)")

    for doc in docs:
        assert doc.exists(), f"{doc} 不存在"
        text = doc.read_text()
        # extract all markdown links [text](path)
        refs = rel_link_re.findall(text)
        # 过滤: 只保留 relative paths, 跳过 http/file-absolute/锚点
        rel_refs = [
            r.strip() for r in refs
            if r.strip() and not r.startswith("http")
            and not r.startswith("file:")
            and not r.startswith("#")
        ]
        assert rel_refs, f"{doc.name} 无任何相对路径引用"

        for ref in rel_refs:
            target = (doc.parent / ref).resolve()
            assert target.exists(), (
                f"{doc.name} 引用 broken path: {ref} → {target} (不存在)"
            )
            # 必须在 repo 范围内 (防止 link 越界)
            try:
                target.relative_to(REPO_ROOT)
            except ValueError:
                pytest.fail(f"{doc.name} 引用逃出 repo: {ref} → {target}")


def test_data_baked_db_exists() -> None:
    """Verify data/final/finance.db 存在 — Dockerfile 的 build-time COPY 才能拷贝真东西。

    报告当前尺寸, 确认双层策略的 image 内置层有真实数据。
    """
    db = REPO_ROOT / "data" / "final" / "finance.db"
    assert db.exists(), f"{db} 不存在 — Dockerfile 的 build-time COPY 会失败"
    size_kb = db.stat().st_size / 1024
    assert 200 < size_kb < 1024, (
        f"finance.db 尺寸异常: {size_kb:.1f}KB — 期望 ~300KB (628 questions)"
    )
