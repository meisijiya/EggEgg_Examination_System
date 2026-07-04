/**
 * 业务 API 调用 — 按后端 endpoints 分组。
 */
import client from './client';
import { fetchSSE } from './sse';
import type {
  DashboardResponse,
  ExamResult,
  ExamSnapshot,
  ExplainRequest,
  ExplainResponse,
  HealthResponse,
  LoginRequest,
  LoginResponse,
  ReviewQueueResponse,
  ReviewUpdateRequest,
  ReviewUpdateResponse,
  StartExamResponse,
  SubmitExamRequest,
  SubmitExamResponse,
} from '@/types/api';

/**
 * SSE 请求的前缀 — 必须与 axios client 的 `baseURL` 行为完全一致：
 * - dev:  Vite proxy 把 `/api` → `http://localhost:8000`（自动剥前缀）
 * - prod: uvicorn 直连同域，**不**带 /api（否则 404）
 *
 * 与 client.ts:20 的判断保持一致。Vite 编译时 import.meta.env.DEV
 * 被静态替换为 true/false，prod build = 空字符串。
 */
const SSE_BASE = import.meta.env.DEV ? '/api' : '';

// ---------- Auth ----------

/**
 * 单密码登录（USER / ADMIN 一并支持 — 后端根据密码区分角色）。
 *
 * 参数:
 *   password: 用户输入的密码
 * 返回:
 *   LoginResponse（含 JWT + role）
 */
export async function login(password: string): Promise<LoginResponse> {
  const body: LoginRequest = { password };
  const { data } = await client.post<LoginResponse>('/auth/login', body);
  return data;
}

// ---------- Exams ----------

/**
 * 启动一次模拟考(服务端出题 + 写 attempt_answers 占位)。
 *
 * 参数:
 *   mode: 'standard'(默认,章节×题型×难度加权)/ 'mixed'(混合模式,
 *         ~30% 题 AI 改编 — fix-20)。
 *
 * 注意:axios 默认 timeout 15s 对 mixed 模式不够(mixed 后端要串行
 * 调 LLM ~13 次,实测 ~30-200s,worst case ~3min)。按 mode 单独覆盖
 * timeout。
 */
export async function startExam(
  mode: 'standard' | 'mixed' = 'standard',
): Promise<StartExamResponse> {
  // mixed 后端 worst case:13 LLM calls × 15s 超时 + 网络抖动 ≈ 3min
  const timeoutMs = mode === 'mixed' ? 180_000 : 15_000;
  const { data } = await client.post<StartExamResponse>(
    '/exams/start',
    { mode },
    { timeout: timeoutMs },
  );
  return data;
}

/**
 * 删除一次模拟考记录（DELETE /exams/{id}，fix-22 新增）。
 *
 * 副作用：级联删除 attempt_answers；返回 204 No Content。
 *
 * 异常:
 *   404 — attempt 不存在；其它 — 透传后端 detail。
 */
export async function deleteExam(attemptId: number): Promise<void> {
  await client.delete(`/exams/${attemptId}`);
}

/**
 * 拉取试卷快照（断线重连用），含已填答案。
 */
export async function getExam(attemptId: number): Promise<ExamSnapshot> {
  const { data } = await client.get<ExamSnapshot>(`/exams/${attemptId}`);
  return data;
}

/**
 * 提交答案 + 触发判分。
 */
export async function submitExam(
  attemptId: number,
  payload: SubmitExamRequest,
): Promise<SubmitExamResponse> {
  const { data } = await client.post<SubmitExamResponse>(
    `/exams/${attemptId}/submit`,
    payload,
  );
  return data;
}

/**
 * 拉取成绩详情（result 接口）。
 */
export async function getResult(attemptId: number): Promise<ExamResult> {
  const { data } = await client.get<ExamResult>(`/exams/${attemptId}/result`);
  return data;
}

// ---------- Explain ----------

/**
 * 流式 AI 讲解（async generator）— 走真实 SSE。
 *
 * 后端事件序列（与 `app/api/explain.py::_stream_explain_response` 对齐）：
 *   1. `{done:false, event:"start", question_id:N}` — 流开始标记
 *   2. 多个 `{done:false, event:"delta", delta:"<chars>"}` — LLM 原始片段
 *      （累积起来是 DeepSeek 吐的 JSON 字符串，含 summary/explanation/key_points/...）
 *   3. `{done:true, available, question_id, reference_answer, analysis}` — 终止
 *      - `available:false` 或带 `error` 字段 → 前端走 fallback
 *
 * 参数:
 *   attemptId  — 当前考试 ID
 *   questionId — 题目 ID
 *   level      — `standard` / `detailed`
 *
 * 返回:
 *   AsyncGenerator，逐个 yield 后端推送的 SSE 事件对象（任意字段）。
 */
export async function* explainQuestionStream(
  attemptId: number,
  questionId: number,
  level: 'standard' | 'detailed' = 'standard',
): AsyncGenerator<Record<string, unknown>> {
  const token = localStorage.getItem('fes_token') ?? '';
  yield* fetchSSE(`${SSE_BASE}/exams/${attemptId}/explain`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ question_id: questionId, level }),
  });
}

/**
 * AI 讲解（保留旧 stub）— 在 SSE 失败 / 未配 DeepSeek key 时作为 fallback 入口。
 *
 * 正常路径走 `explainQuestionStream`；ExplainPanel 在 SSE 流错误时可能切回此接口
 * 拿参考答案 + 官方解析做兜底展示。
 */
export async function explainQuestion(
  attemptId: number,
  req: ExplainRequest,
): Promise<ExplainResponse> {
  const { data } = await client.post<ExplainResponse>(
    `/exams/${attemptId}/explain`,
    req,
  );
  return data;
}

// ---------- Dashboard ----------

/**
 * 仪表盘数据 — 历次成绩 + 趋势 + 章节雷达。
 */
export async function getDashboard(): Promise<DashboardResponse> {
  const { data } = await client.get<DashboardResponse>('/dashboard');
  return data;
}

// ---------- Admin ----------

/**
 * Review 队列（需 admin token）。
 */
export async function getReviewQueue(): Promise<ReviewQueueResponse> {
  const { data } = await client.get<ReviewQueueResponse>('/admin/review/queue');
  return data;
}

/**
 * 修正题目（需 admin token）。
 */
export async function updateQuestion(
  questionId: number,
  payload: ReviewUpdateRequest,
): Promise<ReviewUpdateResponse> {
  const { data } = await client.post<ReviewUpdateResponse>(
    `/admin/review/questions/${questionId}`,
    payload,
  );
  return data;
}

// ---------- Health ----------

/**
 * 健康检查（无需鉴权）。
 */
export async function getHealth(): Promise<HealthResponse> {
  const { data } = await client.get<HealthResponse>('/health');
  return data;
}
