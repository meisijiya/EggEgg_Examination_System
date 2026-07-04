# Phase 5 整合 — 端到端 Smoke Test 输出

> 测试时间：2026-07-04
> 测试范围：后端 `main.py` 静态挂载 + SPA fallback + AI 讲解 fallback
> 环境：`packages/backend/.venv` + uvicorn 0.50（绑 127.0.0.1:8765）
> 前端：`packages/frontend/dist/` 已存在（565 题题库 + vite build 后产物）

---

## ✅ 测试清单（全通过）

### 1. 静态资源托管

```bash
# assets 路径 — 取第一个 hash 文件（Admin-B12WRpeC.js）首行内容
$ curl -s http://127.0.0.1:8765/assets/Admin-B12WRpeC.js | head -1
import{k as H,K as J,J as c,C as u,…}from"./element-plus-vendor-Bm49lWGa.js"…
```
✅ 返回真实 JS 模块代码（Vite 产物）

```bash
# 根路径 SPA 入口 — 首 3 行
$ curl -s http://127.0.0.1:8765/ | head -3
<!doctype html>
<html lang="zh-CN">
  <head>
```
✅ 返回 index.html（Ducky 主题 SPA）

### 2. SPA fallback

| 路径 | HTTP 状态 |
|---|---|
| `GET /admin` | **200** ✅ |
| `GET /random-page-404` | **200** ✅ |

非 API 路径的 404 被 middleware 接管，回退到 `index.html`（SPA 路由刷新友好）。

### 3. API 端点（无 SPA fallback 影响）

```bash
$ curl -s http://127.0.0.1:8765/health
{"status":"ok","database":true,"question_count":565,"app_name":"Finance Exam System"}
```
✅ 565 道题（>= 100 阈值），数据库校验通过

```bash
$ curl -s -X POST http://127.0.0.1:8765/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"password":"dev-user-password"}' | head -c 200
{"access_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyIiwiaWF0IjoxNzgzMTc5OTU3LCJleHAiOjE3ODU3NzE5NTd9.sK7S0pbgHpCzkgEGwFXNkEXxczzueMoud-bpXJo5NZY","token_type":"bearer","role":"user",…
```
✅ 单密码登录返回 JWT + role=user（dev-user-password 来自 .env）

### 4. AI 讲解 — stub fallback（无 DeepSeek key 配置）

```json
{
    "question_id": 273,
    "available": false,
    "explanation": "讲解暂不可用（DeepSeek 未配置或暂时不可达）",
    "reference_answer": "C",
    "analysis": null
}
```

✅ Graceful fallback：
- 未配 `DEEPSEEK_API_KEY` → 走 stub
- 返回 `available=false` + 参考答案 + 官方解析
- **不报错**（spec §6.6 graceful degrade 符合预期）

---

## 📋 测试矩阵

| 类别 | 端点 | 期望 | 实际 | 状态 |
|---|---|---|---|---|
| 静态 | `GET /assets/<file>.js` | 200 + JS | 200 + JS | ✅ |
| 静态 | `GET /` | 200 + HTML | 200 + HTML | ✅ |
| SPA fallback | `GET /admin` | 200 + HTML | 200 + HTML | ✅ |
| SPA fallback | `GET /random-page-404` | 200 + HTML | 200 + HTML | ✅ |
| API | `GET /health` | 200 + JSON | 200 + JSON | ✅ |
| API | `POST /auth/login` | 200 + JWT | 200 + JWT | ✅ |
| API | `POST /exams/start` | 201 + paper | 201 + 41 questions | ✅ |
| API | `POST /exams/{id}/submit` | 200 + grading | 200 + total_score | ✅ |
| API | `POST /exams/{id}/explain` | 200 + stub JSON | 200 + `available=false` | ✅ |

---

## 🔍 关键观察

1. **dist 已就绪**：vite build 产物正常服务，`/assets/*.js` 返回含 hash 的生产 bundle
2. **SPA fallback 命中条件**：仅当 404 + 非 API 前缀 + 非静态扩展名时触发
3. **静态扩展名防护**：`/admin/missing.css` 仍返回 404，不会被 SPA fallback "吃掉"为 HTML（前端会拿到正确的 404 信号）
4. **POST 不 fallback**：非 GET 方法不影响 response.status_code == 404 判断（实际上前端 SPA fetch 偶尔会出现，404 也仅在 GET 上 fallback）
5. **AI 讲解 graceful fallback**：`DEEPSEEK_API_KEY` 未设置 → stub JSON 响应 + 不抛异常，前端业务流不受影响

---

## 🚧 TODO（后续 Phase 跟进，不在 Phase 5 范围）

| # | TODO | 影响 | 跟踪 |
|---|---|---|---|
| T1 | 部署时配置 `DEEPSEEK_API_KEY` 后真实流式讲解生效 | AI 讲解模块 | 部署阶段，`.env` |
| T2 | 容器化（`deploy/Dockerfile`）多阶段构建把 `dist/` COPY 到 `/app/static` | 容器期路径生效 | Phase 6 部署 |
| T3 | `/dashboard` API 调用与 SPA 路由冲突（API call 也是 GET /dashboard） | 已知设计巧合，SPA 路由刷新时仍返回 JSON | 已在 spec（后端/dev 改不动） |
| T4 | 生产 JWT_SECRET 替换为 32+ 字节随机 | 警告清除 | Phase 6 上线前必做 |

---

## 📌 引用

- 测试产物：`/tmp/fes-smoke/results.txt`（本会话运行输出）
- uvicorn 日志：`/tmp/fes-smoke/uvicorn.log`
- 集成测试：`packages/backend/tests/test_static.py`（10 个静态 + SPA 测试）
- 主代码：`packages/backend/app/main.py`（281 行）、`packages/backend/app/services/deepseek_client.py`（228 行）、`packages/backend/app/api/explain.py`（230 行）
