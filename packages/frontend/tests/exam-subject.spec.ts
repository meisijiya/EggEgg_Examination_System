/**
 * currentSubject 持久化测试 — fix-30a。
 *
 * 覆盖:
 *  - setSubject 写入 store + localStorage
 *  - clearSubject 清 store + localStorage
 *  - 初始化时从 localStorage 恢复
 *  - localStorage 损坏值兜底(null / 非法 JSON)
 *  - startNew 默认从 currentSubject.id 取 subject_id
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useExamStore } from '@/stores/exam';
import type { StartExamResponse, Subject } from '@/types/api';
import client from '@/api/client';
import { TOKEN_KEY } from '@/api/client';

const CURRENT_KEY = 'fes_current_subject';

const fakeSubject: Subject = { id: 'fin-mgmt', name: '财务管理' };
const fakeSubject2: Subject = { id: 'corp-strat', name: '公司战略和风险管理' };

function makeFakeStart(): StartExamResponse {
  return {
    attempt_id: 99,
    started_at: '2026-07-05T08:00:00Z',
    time_limit_minutes: 120,
    total_score: 110,
    questions: [],
  };
}

describe('exam store currentSubject(fix-30a)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.restoreAllMocks();
    localStorage.setItem(TOKEN_KEY, 'fake-token');
  });

  it('初始 currentSubject 为 null', () => {
    const store = useExamStore();
    expect(store.currentSubject).toBeNull();
  });

  it('setSubject 写入 store + localStorage', () => {
    const store = useExamStore();
    store.setSubject(fakeSubject);
    expect(store.currentSubject).toEqual(fakeSubject);
    const raw = localStorage.getItem(CURRENT_KEY);
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw!)).toEqual(fakeSubject);
  });

  it('clearSubject 清 store + localStorage', () => {
    const store = useExamStore();
    store.setSubject(fakeSubject);
    store.clearSubject();
    expect(store.currentSubject).toBeNull();
    expect(localStorage.getItem(CURRENT_KEY)).toBeNull();
  });

  it('初始化时从 localStorage 恢复 currentSubject', () => {
    localStorage.setItem(CURRENT_KEY, JSON.stringify(fakeSubject));
    // 重建 pinia 触发 store 初始化
    setActivePinia(createPinia());
    const store = useExamStore();
    expect(store.currentSubject).toEqual(fakeSubject);
  });

  it('localStorage 损坏值兜底 — JSON 解析失败时 currentSubject 仍为 null', () => {
    localStorage.setItem(CURRENT_KEY, '{not valid json');
    setActivePinia(createPinia());
    const store = useExamStore();
    expect(store.currentSubject).toBeNull();
  });

  it('localStorage 字段缺失 — 非法 Subject 字段被忽略', () => {
    localStorage.setItem(CURRENT_KEY, JSON.stringify({ id: 'x' })); // 缺 name
    setActivePinia(createPinia());
    const store = useExamStore();
    expect(store.currentSubject).toBeNull();
  });

  it('切换 subject 后 oldSubject 不影响新引用', () => {
    const store = useExamStore();
    store.setSubject(fakeSubject);
    store.setSubject(fakeSubject2);
    expect(store.currentSubject?.id).toBe('corp-strat');
    const raw = JSON.parse(localStorage.getItem(CURRENT_KEY)!);
    expect(raw.id).toBe('corp-strat');
  });

  it('startNew 透传 currentSubject.id 到 startExam API', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    const store = useExamStore();
    store.setSubject(fakeSubject2);
    await store.startNew('standard');
    const [, body] = spy.mock.calls[0];
    expect(body).toMatchObject({ subject_id: 'corp-strat', mode: 'standard' });
  });

  it('startNew 无 currentSubject 时兜底为 "fin-mgmt"', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    const store = useExamStore();
    // 不调 setSubject
    await store.startNew('mixed');
    const [, body] = spy.mock.calls[0];
    expect(body).toMatchObject({ subject_id: 'fin-mgmt', mode: 'mixed' });
  });

  it('startNew 显式传 subjectId 优先于 store', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    const store = useExamStore();
    store.setSubject(fakeSubject);
    await store.startNew('standard', 'override-subj');
    const [, body] = spy.mock.calls[0];
    expect(body).toMatchObject({ subject_id: 'override-subj' });
  });

  it('reset 不清 currentSubject — 跨考试保留选择', () => {
    const store = useExamStore();
    store.setSubject(fakeSubject);
    store.reset();
    expect(store.currentSubject).toEqual(fakeSubject);
  });
});