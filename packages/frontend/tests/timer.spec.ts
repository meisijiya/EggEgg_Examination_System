/**
 * 计时器倒计时测试 — 验证 deadlineMs() 的计算逻辑。
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useExamStore } from '@/stores/exam';
import type { StartExamResponse } from '@/types/api';

function makeStartAt(offsetMs: number): StartExamResponse {
  // 启动时间 = 现在 + offset
  const iso = new Date(Date.now() + offsetMs).toISOString();
  return {
    attempt_id: 200,
    started_at: iso,
    time_limit_minutes: 120,
    total_score: 110,
    questions: [],
  };
}

describe('计时器倒计时', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-04T10:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('deadlineMs = startedAt + 120 分钟', () => {
    const store = useExamStore();
    store.applyStartResponse(makeStartAt(0));
    const expected = new Date('2026-07-04T10:00:00Z').getTime() + 120 * 60_000;
    expect(store.deadlineMs()).toBe(expected);
  });

  it('timeLimitMinutes 默认 120', () => {
    const store = useExamStore();
    expect(store.timeLimitMinutes).toBe(120);
  });

  it('deadlineMs 在 started_at 之前为 0（未启动）', () => {
    const store = useExamStore();
    expect(store.deadlineMs()).toBe(0);
  });

  it('applyStartResponse 同步更新 time_limit_minutes', () => {
    const store = useExamStore();
    const resp = makeStartAt(0);
    resp.time_limit_minutes = 60;
    store.applyStartResponse(resp);
    expect(store.timeLimitMinutes).toBe(60);
    const expected = new Date('2026-07-04T10:00:00Z').getTime() + 60 * 60_000;
    expect(store.deadlineMs()).toBe(expected);
  });
});
