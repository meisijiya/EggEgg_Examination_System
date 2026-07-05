/**
 * 科目 + Admin review queue API 客户端 — fix-30a + Phase 5 fix-7。
 *
 * 端点:
 * - GET  /subjects                              → listSubjects()
 * - GET  /admin/ai-generated-questions           → getAiGeneratedQuestions()
 * - POST /admin/approve-question/{qid}           → approveQuestion()
 * - POST /admin/reject-question/{qid}            → rejectQuestion()
 *
 * Phase 5 fix-7: 后端 /subjects 端点已实现, catch 兜底保留以防极端网络故障。
 */
import client from './client';
import type {
  AiGeneratedQuestionsResponse,
  Subject,
} from '@/types/api';

/** localStorage 中"上次选择的 subject" 的 key。 */
export const SUBJECT_STORAGE_KEY = 'fes_last_subject_id';

/**
 * 列示所有已发布科目 — 学员顶栏 SubjectSwitcher 调用。
 *
 * 失败兜底:返回单科目 ['财务管理'],避免 UI 因 404/500 崩溃。
 * 后续如果接多个科目,后端返回 [{id:'fin-mgmt',...}, {id:'corp-strat',...}] 即可,
 * 不用改前端选择逻辑。
 */
export async function listSubjects(): Promise<Subject[]> {
  try {
    const { data } = await client.get<Subject[]>('/subjects');
    if (Array.isArray(data) && data.length > 0) return data;
    return [{ id: 'fin-mgmt', name: '财务管理', question_count: 0 }];
  } catch {
    // ponytail: 极端网络故障时兜底 — 不抛错,UI 仍可工作
    return [{ id: 'fin-mgmt', name: '财务管理', question_count: 0 }];
  }
}

/**
 * Admin:列出所有待 review 的 AI 生成题(仅 status='pending')。
 */
export async function getAiGeneratedQuestions(): Promise<AiGeneratedQuestionsResponse> {
  const { data } = await client.get<AiGeneratedQuestionsResponse>(
    '/admin/ai-generated-questions',
    { params: { status: 'pending' } },
  );
  return data;
}

/**
 * Admin:批准一条 AI 生成题 — 写库 + 状态变 approved。
 */
export async function approveQuestion(qid: number): Promise<void> {
  await client.post(`/admin/approve-question/${qid}`);
}

/**
 * Admin:拒绝一条 AI 生成题 — 写库 + 状态变 rejected,记录原因供后续复盘。
 */
export async function rejectQuestion(qid: number, reason: string): Promise<void> {
  await client.post(`/admin/reject-question/${qid}`, { reason });
}