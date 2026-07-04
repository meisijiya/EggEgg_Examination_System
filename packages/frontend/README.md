# Finance Exam System — Frontend (Phase 3 SPA)

Vue 3 + Vite + TypeScript + Element Plus + ECharts 单页应用。

> **状态**：Phase 3（T3.1-T3.13）完成。
> **核心功能**：登录 → 开始模拟考 → 答题（计时 + 草稿）→ 交卷 → 成绩（雷达 + AI 讲解）→ 仪表盘（趋势）。
> **附加**：管理员题目 review 入口（开发期独立鉴权）。

---

## 📦 项目结构

```
packages/frontend/
├── src/
│   ├── api/                  # axios 客户端 + 业务 API
│   │   ├── client.ts         # 拦截器（token 注入 + 401 跳登录）
│   │   └── index.ts          # 按端点分组的业务调用
│   ├── components/           # 公共组件
│   │   ├── ExplainPanel.vue  # AI 讲解面板（流式渲染 + 失败回退）
│   │   └── QuestionCard.vue  # 单题渲染（学员视图 / 结果视图）
│   ├── pages/                # 路由页面
│   │   ├── Login.vue         # /login
│   │   ├── Home.vue          # / (首页)
│   │   ├── ExamIntro.vue     # /exam/:id/intro
│   │   ├── ExamPlay.vue      # /exam/:id/play
│   │   ├── ExamResult.vue    # /exam/:id/result
│   │   ├── Dashboard.vue     # /dashboard
│   │   └── Admin.vue         # /admin
│   ├── router/               # Vue Router + 鉴权守卫
│   ├── stores/               # Pinia
│   │   ├── auth.ts           # 登录态 + JWT 持久化
│   │   ├── exam.ts           # 当前考试状态 + 草稿 + 计时
│   │   └── dashboard.ts      # 仪表盘数据
│   ├── types/                # TypeScript 类型（与后端 schema 对齐）
│   ├── styles/global.css     # 全局样式
│   ├── App.vue               # 根组件（顶部导航 + router-view）
│   └── main.ts               # 入口（Pinia + Router + Element Plus）
├── tests/                    # Vitest 测试
│   ├── question-card.spec.ts # 答题卡片切换
│   ├── timer.spec.ts         # 计时器倒计时
│   ├── explain-panel.spec.ts # AI 讲解面板流式渲染
│   └── echarts-mount.spec.ts # ECharts 组件挂载
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts            # Vite + 代理 /api → :8000
├── vitest.config.ts          # Vitest 独立配置
└── README.md
```

---

## 🚀 本地启动

### 前置依赖

- Node.js 20+（推荐 24 LTS）
- 后端 `packages/backend/` 已启动在 `http://localhost:8000`

### 1. 安装依赖

```bash
cd packages/frontend
npm install
```

### 2. 启动开发服务器

```bash
npm run dev
```

启动后访问 `http://localhost:5173`，Vite 会自动把 `/api/*` 代理到后端 `:8000`。

### 3. 生产构建

```bash
npm run build
```

产物输出到 `dist/`（约 2.1MB：element-plus 1MB + echarts 565KB + 业务 100KB）。

### 4. 运行测试

```bash
npm test
```

当前 **15 个测试用例全部通过**：
- 答题卡片切换（6 个）
- 计时器倒计时（4 个）
- AI 讲解面板流式渲染（3 个）
- ECharts 组件挂载（2 个）

---

## 🔌 路由列表

| 路径 | 名称 | 鉴权 | 描述 |
|---|---|---|---|
| `/login` | login | 公开 | 单密码登录 |
| `/` | home | 需登录 | 首页（最近成绩 + 开始模拟考） |
| `/exam/:id/intro` | exam-intro | 需登录 | 考试介绍（时长 / 题型分布） |
| `/exam/:id/play` | exam-play | 需登录 | 答题页（分题型卡片 + 计时器） |
| `/exam/:id/result` | exam-result | 需登录 | 成绩页（雷达 + 评语 + AI 讲解） |
| `/dashboard` | dashboard | 需登录 | 仪表盘（趋势 + 雷达 + 列表） |
| `/admin` | admin | **admin** | 题目 review（独立密码） |

---

## 🧠 关键设计要点

### 1. 鉴权 + JWT 持久化

- 启动时从 `localStorage` 恢复 token + role
- axios 请求拦截器自动注入 `Authorization: Bearer <token>`
- 401 响应自动清 token + 跳 `/login`
- 路由守卫区分"需登录"和"admin-only"

### 2. 考试状态管理（Pinia store）

- `applyStartResponse()` / `loadExisting()` 两种入口
- 学员答案变更走 `setAnswer()`，30s 兜底写 localStorage
- 草稿按 `attempt_id` 分键（`fes_draft_<id>`），断线重连自动恢复
- `deadlineMs()` 基于服务端 `started_at` 算截止时间戳
- 答题页 1s tick 触发倒计时 → 0 自动提交

### 3. AI 讲解（SSE 兼容）

- spec §6.6 计划接 SSE，但当前后端是 stub JSON
- `ExplainPanel.vue` 做"流式模拟"：拿到 explanation 后每 40ms append 12 字
- 失败回退：显示"讲解暂不可用" + 参考答案 + 解析 + 兜底 alert
- 切换 SSE 时只需把 `streamText` 替换为 `fetch + ReadableStream`

### 4. 图表按需引入

- ECharts 用 `echarts/core` 模式：只 import 需要的 chart + component + renderer
- 生产产物：565KB（gzip 188KB）

### 5. Element Plus 全量引入

- 当前是 `app.use(ElementPlus)` 全量（1004KB / gzip 330KB）
- 后续可改按需引入（`unplugin-element-plus`），节省 ~50% 体积

### 6. 响应式

- flex 布局 + 媒体查询
- 移动端（≤768px）单列堆叠，答题卡单列
- viewport meta 禁止缩放（女友用 iPad/手机）

---

## ⚠️ 已知偏差 / TODO

1. **AI 讲解 SSE 未真正接入**：后端是 stub JSON，前端用 `setTimeout` 模拟流式输出。
   真实接入只需把 `ExplainPanel.vue` 的 `streamText` 换成 `fetch + ReadableStream`。

2. **comprehensive 题型 fallback**：后端数据中没有 comprehensive 题目，runtime 抽题时 comprehensive slot 自动 fallback 到 calc（type 字段保留 calc）。
   前端用 `runtime` 视角渲染（看到啥 type 渲染啥 type），不主动提示 fallback。

3. **总分 110 vs spec 100**：spec 标注"= 100 分"，但实际数值（15×2 + 10×3 + 10×1 + 4×5 + 2×10）= 110 分。
   前后端都按 110 分处理。

4. **Element Plus 全量引入**：约 1MB 体积；如需瘦身可改按需引入（但配置成本略高，留待 polish）。

5. **Admin 密码复用**：`/admin` 登录复用 `/auth/login`（后端按密码区分 role），不是真正独立密码路径。
   改造成本：加 `ADMIN_PASSWORD` 专用 API。当前够用。

6. **草稿恢复无服务端同步**：纯 localStorage 草稿；多设备登录会失去草稿（YAGNI 不做服务端草稿）。

7. **AI 讲解 miss_points / study_tip 未展示**：当前 stub 不返回这些字段，UI 已就绪。
   后端真接 DeepSeek 后直接生效。

---

## 📚 引用文档

- 设计 Spec：`docs/superpowers/specs/2026-07-04-finance-exam-system-design.md`
- OpenSpec tasks：`openspec/changes/finance-exam-system-mvp/tasks.md`
- 后端 README：`packages/backend/README.md`
