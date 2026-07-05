/**
 * exam 启动流程测试 — fix-bug (mixed 模式 UX bug)。
 *
 * 覆盖:
 *  - startExam 混合模式 timeout = 90s(防 ~30-60s 后端被 15s axios 切断)
 *  - startExam 标准模式 timeout = 15s(保持原有快速失败)
 *  - exam store 的 startNew 无 token 时抛"登录已过期"
 *  - axios 401 拦截器清 token + 跳 /login?reason=expired
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import client, { TOKEN_KEY, ROLE_KEY } from '@/api/client';
import { startExam } from '@/api';
import { useExamStore } from '@/stores/exam';
import type { StartExamResponse } from '@/types/api';

function makeFakeStart(): StartExamResponse {
  return {
    attempt_id: 99,
    started_at: '2026-07-05T08:00:00Z',
    time_limit_minutes: 120,
    total_score: 110,
    questions: [
      {
        id: 1,
        type: 'single',
        chapter_id: 1,
        chapter_code: 'ch1',
        difficulty: 1,
        stem: 'Q1',
        options: ['A', 'B'],
        score: 2,
        sequence: 1,
      },
    ],
  };
}

describe('startExam timeout 配置(fix-bug)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('mixed 模式 → axios 调用 timeout 配置为 180000ms(覆盖 13 LLM × 15s worst case)', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    await startExam('fin-mgmt', 'mixed');
    expect(spy).toHaveBeenCalledTimes(1);
    const [, , cfg] = spy.mock.calls[0];
    expect(cfg).toMatchObject({ timeout: 180_000 });
  });

  it('standard 模式 → axios 调用 timeout 配置为 15000ms(保持原状)', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    await startExam('fin-mgmt', 'standard');
    const [, , cfg] = spy.mock.calls[0];
    expect(cfg).toMatchObject({ timeout: 15_000 });
  });

  it('默认参数 → standard 行为(timeout=15s)', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    await startExam('fin-mgmt');
    const [, , cfg] = spy.mock.calls[0];
    expect(cfg).toMatchObject({ timeout: 15_000 });
  });

  it('请求 body 含 subject_id + mode 字段(fix-30a)', async () => {
    const spy = vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    await startExam('fin-mgmt', 'mixed');
    const [, body] = spy.mock.calls[0];
    expect(body).toEqual({ subject_id: 'fin-mgmt', mode: 'mixed' });
  });
});

describe('exam store startNew token 防御(fix-bug)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('无 token → 抛"登录已过期" + 不发请求', async () => {
    const spy = vi.spyOn(client, 'post');
    const exam = useExamStore();
    await expect(exam.startNew('mixed')).rejects.toThrow(/登录已过期/);
    expect(spy).not.toHaveBeenCalled();
  });

  it('有 token → 正常调 startExam 并写入 store', async () => {
    localStorage.setItem(TOKEN_KEY, 'fake-token');
    vi.spyOn(client, 'post').mockResolvedValue({
      data: makeFakeStart(),
      status: 201,
      statusText: 'Created',
      headers: {},
      config: {} as never,
    });
    const exam = useExamStore();
    const resp = await exam.startNew('mixed');
    expect(resp.attempt_id).toBe(99);
    expect(exam.attemptId).toBe(99);
    expect(exam.questions).toHaveLength(1);
  });
});

describe('axios 401 拦截器(fix-bug)', () => {
  let origLocation: Location;
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    origLocation = window.location;
    // 用直接替换 location 描述对象的方式拦截(happy-dom 简化版)
    Object.defineProperty(window, 'location', {
      writable: true,
      configurable: true,
      value: {
        href: '/exam/1/play',
        pathname: '/exam/1/play',
        origin: 'http://localhost',
        host: 'localhost',
        hostname: 'localhost',
        port: '',
        protocol: 'http:',
        search: '',
        hash: '',
        ancestorOrigins: null,
        assign: vi.fn(),
        replace: vi.fn(),
        reload: vi.fn(),
        toString: () => 'http://localhost/exam/1/play',
      },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      writable: true,
      configurable: true,
      value: origLocation,
    });
  });

  /**
   * 直接调用注册在 client 上的"response 拦截器"的 rejected handler。
   *
   * rationale:走 client.post + spy adapter 在 happy-dom + axios 0.28 下
   * 容易绕过拦截器(axios v1 instance methods 通过 prototype 走 pipeline,
   * vi.spyOn(instance, 'post') 直接替换 instance 自身方法 → 拦截器不触发)。
   * 直接执行已注册的 rejected handler 是测试拦截器逻辑的最可靠方式。
   */
  function callResponseInterceptor(err: unknown): Promise<unknown> {
    type Handler = { rejected: (e: unknown) => Promise<unknown> };
    type HandlersList = { handlers: Handler[] };
    const respInterceptor = (client.interceptors as unknown as {
      response: HandlersList;
    }).response;
    const rejectedHandler = respInterceptor.handlers[0].rejected;
    return Promise.resolve().then(() => rejectedHandler(err));
  }

  it('401 响应 → 清 token + 清 role + 跳 /login?reason=expired', async () => {
    localStorage.setItem(TOKEN_KEY, 'fake-token');
    localStorage.setItem(ROLE_KEY, 'user');
    let caught: unknown;
    try {
      await callResponseInterceptor({
        response: { status: 401, data: { detail: 'Not authenticated' } },
        message: 'Request failed with status code 401',
      });
    } catch (e) {
      caught = e;
    }
    const apiErr = caught as { status?: number; message?: string };
    expect(apiErr?.status).toBe(401);
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(localStorage.getItem(ROLE_KEY)).toBeNull();
    expect(window.location.href).toBe('/login?reason=expired');
  });

  it('当前已在 /login → 401 不重复跳转(避免无限重定向)', async () => {
    Object.defineProperty(window, 'location', {
      writable: true,
      configurable: true,
      value: { ...origLocation, href: '/login', pathname: '/login' },
    });
    localStorage.setItem(TOKEN_KEY, 'fake-token');
    try {
      await callResponseInterceptor({
        response: { status: 401, data: { detail: 'Not authenticated' } },
        message: 'Request failed with status code 401',
      });
    } catch {
      // ignore
    }
    expect(window.location.href).toBe('/login');
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it('非 401 错误 → 抛 ApiError + 不清 token + 不跳 /login', async () => {
    localStorage.setItem(TOKEN_KEY, 'fake-token');
    let caught: unknown;
    try {
      await callResponseInterceptor({
        response: { status: 500, data: { detail: 'server error' } },
        message: 'Request failed with status code 500',
      });
    } catch (e) {
      caught = e;
    }
    const apiErr = caught as { status?: number; message?: string };
    expect(apiErr?.status).toBe(500);
    expect(apiErr?.message).toBe('server error');
    expect(localStorage.getItem(TOKEN_KEY)).toBe('fake-token');
    expect(window.location.href).toBe('/exam/1/play');
  });

  it('无 response 对象(网络错误) → status=0 + 抛 ApiError', async () => {
    let caught: unknown;
    try {
      await callResponseInterceptor({
        message: 'Network Error',
      });
    } catch (e) {
      caught = e;
    }
    const apiErr = caught as { status?: number; message?: string };
    expect(apiErr?.status).toBe(0);
    expect(apiErr?.message).toBe('Network Error');
  });
});
