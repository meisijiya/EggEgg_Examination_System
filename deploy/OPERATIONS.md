# 🚀 项目部署运维手册

> 接手 EggEgg_Examination_System 后必读。覆盖:一键拉起 → 数据策略 → 日常运维 → 故障排查 → 监控 → 安全审计。
> 重复的部署步骤见 [`README.md`](../README.md) §部署 + [`deploy/Dockerfile`](Dockerfile) + [`deploy/docker-compose.yml`](docker-compose.yml) + [`spec §10`](../docs/superpowers/specs/2026-07-04-finance-exam-system-design.md)。

---

## 1. ☁️ 一键拉起(全新云服务器)

1. 装 Docker 24+ + Compose v2(`docker compose version` 验证)
2. 克隆代码 → `cp .env.example .env` → 编辑 `JWT_SECRET` / `USER_PASSWORD` / `ADMIN_PASSWORD` / `CORS_ORIGINS`
3. 启动:
   ```bash
   docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
   ```
4. **等 30s** 让 uvicorn boot (启动顺序: `alembic upgrade head` → `uvicorn app.main:app`):
   ```bash
   sleep 30
   ```
5. 健康检查:
   ```bash
   curl http://127.0.0.1:8000/health
   ```
   期望响应: `{"status":"ok","database":true,"question_count":628,"app_name":"Finance Exam System"}`
   - `question_count:612` = fin-mgmt 565 + corp-strat 47 (build-time baked in image)
   - 若 `status:"degraded"` 或 `database:false` → 看 §4 故障排查
6. 云反代(SLB/CLB/CF)终止 TLS → 转发 `http://<server-ip>:8000`, 透传 `X-Forwarded-For/Proto`
7. 直跑 nginx 的看 [`deploy/nginx.example.conf`](nginx.example.conf)

> 反代模型图见 `README.md` §部署架构图; 不在云反代前置时务必限制 `127.0.0.1:8000` 端口源 IP。

---

## 2. 📦 数据迁移策略(用户硬决策 = 0)

**决策**: 数据迁移 = 0(全新拉起)。 双层数据策略:

| 层 | 路径 | 来源 | 用途 |
|---|---|---|---|
| **build-time COPY** | `data/parsed/`, `data/distributions/`, `data/qa/`(入 image) | 仓库 git 跟踪 | 题库 metadata 兜底 |
| **runtime VOLUME** | `./data:/app/data:rw` | docker-compose volume 挂载 | `finance.db` / `app.db` 持久化 |

**双库 pattern**(取决于 `build_db.py` 处理):
- `data/final/finance.db` — **题库, 运行时只读**, 重生成=重新跑 `build_db.py`(见 `spec §5.1`)
- `data/app.db` — **应用库, 写入**(考试记录 / 用户进度 / 答案), **必须备份**

> 用户多科时扩展为 `corporate_strategy.db` 等, 每科独立 DB file; build_db.py 通过 `--subject` 切换。

---

## 3. 🔧 日常运维

| 操作 | 命令 |
|---|---|
| 重启 | `docker compose -f deploy/docker-compose.yml restart` |
| 查看日志 | `docker compose -f deploy/docker-compose.yml logs -f --tail=100` |
| 健康检查 | `curl -fsS http://127.0.0.1:8000/health` |
| 备份 app.db | `docker compose exec -T finance-exam sqlite3 /app/data/app.db ".backup '/app/data/app.db.$(date +%Y%m%d)'"` |

### 升级到最新版(完整流程)

```bash
# 1. 拉取最新 main 分支代码
git pull origin main

# 2. 停旧 container (让 volume 释放 + 数据落盘)
cd deploy && docker compose down

# 3. 重新 build 镜像 + 后台启动
docker compose up -d --build

# 4. 等 30s + 健康检查
sleep 30 && curl http://127.0.0.1:8000/health
# 期望: {"status":"ok","database":true,"question_count":628,...}
```

> ⚠️ `docker compose down` **不会删 volume**(`../data:/app/data:rw`), `app.db` + 自定义 `finance.db` 持久化保留。

### 回滚

```bash
git checkout <last-good-sha> && \
  cd deploy && docker compose build --no-cache && docker compose up -d
```

- 资源限制(`docker-compose.yml` `deploy.resources`): CPU `0.8/0.2` / MEM `768M/256M` — 1 vCPU / 1GB 节点调优, 实测 peak 86M, **充足**
- 健康检查失败 3 次 → `restart: unless-stopped` 自动重启
- 日志 `json-file` max-size 10M × 3 file — 防磁盘被填满
- 单 container 名: `egg-egg-exam-system`(compose v2 顶层 `name:`,见 `deploy/docker-compose.yml` L23)

---

## 4. 🔍 故障排查

### Phase 5 实际遇到 + 修过的 3 个 bug(fix-26)

| 症状 | 根因 | 修复 |
|------|------|------|
| `docker build` 失败 `ERR_UNKNOWN_BUILTIN_MODULE` / npm error ENOENT | `Dockerfile` Stage 1 误用 `pnpm install`,但项目是 npm 生态(只有 `package-lock.json`,无 `pnpm-lock.yaml`) | `Dockerfile` L34-43 改 `corepack enable` 前直接用 `npm ci + npm run build`(不动 `package.json`) |
| Container `Restarting (1) Less than a second ago` 日志 `alembic: not found` | `CMD ["sh", "-c", "alembic upgrade head && exec uvicorn ..."]` 跑时 `alembic` / `uvicorn` 不在 PATH(uv sync 装到 `.venv/bin/`) | `Dockerfile` L114 改 `/app/.venv/bin/alembic` + `/app/.venv/bin/uvicorn --app-dir /app`(绝对路径) |
| Container 启动后 `/health` 5xx,日志 `IndexError: list index out of range` in `_resolve_static_dir` | 容器内 `main.py` 用 `parents[3]` 反推 4 级路径到 repo root,但 runtime 文件位置只允许 3 级 parents | `main.py` `_resolve_static_dir` 改容器期优先直接使用 `/app/static`,开发期 fallback `parents[3]`,外层 try/except 兜底 |

### 常规排查

| 症状 | 排查点 |
|---|---|
| 端口冲突 | `ss -tlnp | grep 8000`, 改 `docker-compose.yml ports` 或停占用的 service |
| `/health` 返回 `database:false` | 容器内 `ls -la /app/data/final/`, 检查 finance.db 是否被 volume 覆盖 / 损坏 |
| 502 Bad Gateway | 反代后端 upstream 是否配 `http://<server-ip>:8000`; 容器是否 `unhealthy`(`docker ps`) |
| OOM Killed | `docker stats` 看 mem; 单 SQLite 库 + uvicorn 实测 peak 86M, 768M 限制充足 |
| mixed mode 超时 | `assemble_paper_async` 并发 12 题 × DeepSeek → ~90-300s, 前端 `axios timeout=180s` 见 `spec §11` |
| AI pipeline 失败 | `DEEPSEEK_API_KEY` 缺失 → graceful fallback 到 "参考答案 + 解析", 不影响考试; 见 `spec §12.3.4` |
| corporate-strat AI 出题无题 | `corporate_strategy_ai_generated.jsonl` 经 `auto_approve_ai.py` 后必须 `status=approved` 才能 build_db 加载 |

---

## 5. 📊 监控建议

- **CPU / 内存**: `docker stats finance-exam-system`(持续 < 60% 为健康)
- **磁盘**: `df -h /` + 监控 `data/app.db` 增长(每周 cron 备份并清理 > 30 天)
- **DB size**: 题库 `finance.db` 当前 256 KB(`fin-mgmt`)→ 加 `corp-strat` 后 ~512 KB, 体积不敏感
- **API 错误率**: `docker logs` 抓 `5xx` 频次; `/health` 失败 = 数据层问题
- **AI 调用次数**: DeepSeek 控制台按月导出, mixed mode 是主消耗点(spec §12.4 估算)

---

## 6. 🔐 安全审计

| 检查项 | 命令 / 注意点 |
|---|---|
| `.env.example` vs `.env` | `.env` 不入 git(`.gitignore` 强制), 生产前必须填真实 secrets |
| API key 泄露扫描 | `git ls-files | xargs grep -l "sk-"` 应为空, 命中立刻换 key + 删 git history |
| 镜像重建频率 | 每次 `git pull` 后 `docker compose build` — 不要 `latest` cache 跨版本用 |
| CORS | `.env` 的 `CORS_ORIGINS` 不带通配符 `*`, FastAPI `credentials=true` 不允许 |
| Healthcheck 暴露面 | `127.0.0.1:8000` 仅本地监听, 不直暴露公网, 反代前置 |
| 备份策略 | `crontab 03:00` 跑 `app.db` 备份 + rclone/ossutil 同步到 OSS, 保留 30 天 |
| rebuild 后 volume | 题库 metadata 改 → `--build` 强制; runtime DB 不变 → 不用 `--force-recreate` |

---

**📚 进一步阅读**: `README.md`(部署 + 架构图) / `spec §10.4`(时区规范) / `spec §11`(性能基线) / `packages/backend/scripts/auto_approve_ai.py`(AI 题审批脚本)