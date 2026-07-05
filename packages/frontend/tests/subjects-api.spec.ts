/**
 * subjects.ts API 客户端单元测试 — fix-30a。
 *
 * 覆盖:
 *  - listSubjects 成功 → 返回数组
 *  - listSubjects 后端 404/500 → 兜底返回 [{id:'fin-mgmt', name:'财务管理'}]
 *  - listSubjects 后端返回空数组 → 兜底
 *  - getAiGeneratedQuestions 透传 /admin/ai-generated-questions
 *  - approveQuestion POST /admin/approve-question/{qid}
 *  - rejectQuestion POST /admin/reject-question/{qid} + reason body
 *  - SUBJECT_STORAGE_KEY 常量值正确
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import client from '@/api/client';
import {
  listSubjects,
  getAiGeneratedQuestions,
  approveQuestion,
  rejectQuestion,
  SUBJECT_STORAGE_KEY,
} from '@/api/subjects';
import type {
  AiGeneratedQuestionsResponse,
  Subject,
} from '@/types/api';

describe('subjects.ts API(fix-30a)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('SUBJECT_STORAGE_KEY = fes_last_subject_id', () => {
    expect(SUBJECT_STORAGE_KEY).toBe('fes_last_subject_id');
  });

  it('listSubjects 成功 → 返回数组', async () => {
    const data: Subject[] = [
      { id: 'fin-mgmt', name: '财务管理' },
      { id: 'corp-strat', name: '公司战略' },
    ];
    vi.spyOn(client, 'get').mockResolvedValue({
      data,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as never,
    });
    const r = await listSubjects();
    expect(r).toEqual(data);
  });

  it('listSubjects 后端 500 → 兜底返回 [{fin-mgmt, 财务管理}]', async () => {
    vi.spyOn(client, 'get').mockRejectedValue(new Error('500 server error'));
    const r = await listSubjects();
    expect(r).toEqual([{ id: 'fin-mgmt', name: '财务管理', question_count: 0 }]);
  });

  it('listSubjects 后端返回空数组 → 兜底返回 [{fin-mgmt, 财务管理}]', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({
      data: [],
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as never,
    });
    const r = await listSubjects();
    expect(r.length).toBeGreaterThanOrEqual(1);
    expect(r[0].id).toBe('fin-mgmt');
  });

  it('getAiGeneratedQuestions 调 /admin/ai-generated-questions + status=pending', async () => {
    const data: AiGeneratedQuestionsResponse = { items: [], total: 0 };
    const spy = vi.spyOn(client, 'get').mockResolvedValue({
      data,
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {} as never,
    });
    await getAiGeneratedQuestions();
    const [url, cfg] = spy.mock.calls[0];
    expect(url).toBe('/admin/ai-generated-questions');
    expect(cfg).toMatchObject({ params: { status: 'pending' } });
  });

  it('approveQuestion POST /admin/approve-question/{qid}', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: undefined,
      status: 204,
      statusText: 'No Content',
      headers: {},
      config: {} as never,
    });
    await approveQuestion(42);
    const [url] = spy.mock.calls[0];
    expect(url).toBe('/admin/approve-question/42');
  });

  it('rejectQuestion POST /admin/reject-question/{qid} + reason body', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: undefined,
      status: 204,
      statusText: 'No Content',
      headers: {},
      config: {} as never,
    });
    await rejectQuestion(99, '题目错误');
    const [url, body] = spy.mock.calls[0];
    expect(url).toBe('/admin/reject-question/99');
    expect(body).toEqual({ reason: '题目错误' });
  });
});