/**
 * ExamResult.vue 渲染测试 — fix-23a P0 critical。
 *
 * 覆盖:
 *  - 单选题:GradedAnswerDetail.options → QuestionCard.options 透传
 *    (结果页 "A. xxx" 选项列表展示)
 *  - 多选题:同上,正确选项被 QuestionCard 标识
 *  - 判断题:options 透传后由 QuestionCard 渲染 '对/错'
 *  - 计算题:options=null → 不渲染选项
 *  - 数据契约:GradedAnswerDetail.options 字段存在 (frontend api.ts 类型)
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import { createRouter, createMemoryHistory } from 'vue-router';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import ExamResult from '@/pages/ExamResult.vue';
import * as apiMod from '@/api';
import type { ExamResult as ExamResultType, GradedAnswerDetail } from '@/types/api';

// mock ECharts init(避免 happy-dom 中 canvas 报错)
vi.mock('echarts/core', async () => {
  const actual = await vi.importActual<typeof import('echarts/core')>('echarts/core');
  return {
    ...actual,
    init: vi.fn(() => ({
      setOption: vi.fn(),
      dispose: vi.fn(),
      resize: vi.fn(),
    })),
  };
});

// mock API: getResult
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof apiMod>('@/api');
  return {
    ...actual,
    getResult: vi.fn(),
  };
});

function makeAnswer(overrides: Partial<GradedAnswerDetail>): GradedAnswerDetail {
  return {
    question_id: 1,
    sequence: 1,
    type: 'single',
    chapter_code: 'ch1',
    stem: '题干',
    user_answer: 'A',
    correct_answer: 'A',
    is_correct: true,
    awarded_score: 2,
    full_score: 2,
    comment: '正确',
    ...overrides,
  };
}

function makeResult(answers: GradedAnswerDetail[]): ExamResultType {
  return {
    attempt_id: 1,
    started_at: '2026-07-05T08:00:00Z',
    submitted_at: '2026-07-05T10:00:00Z',
    total_score: 100,
    score_by_chapter: { ch1: 50, ch2: 50 },
    score_by_type: { single: 30, multi: 30, judge: 10, calc: 20, comprehensive: 10 },
    answers,
  };
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div></div>' } },
      { path: '/dashboard', component: { template: '<div></div>' } },
      { path: '/exam/:id/result', component: ExamResult },
      { path: '/exam/:id/play', component: { template: '<div></div>' } },
    ],
  });
}

describe('ExamResult options 透传 (fix-23a P0)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('单选题:options 列表透传到 QuestionCard,显示 "A. 选项内容"', async () => {
    const singleOpts = ['选项A内容', '选项B内容', '选项C内容', '选项D内容'];
    const answer = makeAnswer({
      question_id: 1,
      type: 'single',
      options: singleOpts,
      user_answer: 'A',
      correct_answer: 'A',
      is_correct: true,
    });
    vi.mocked(apiMod.getResult).mockResolvedValue(makeResult([answer]));

    const router = makeRouter();
    router.push('/exam/1/result');
    await router.isReady();

    const wrapper = mount(ExamResult, {
      global: { plugins: [ElementPlus, router] },
    });
    await nextTick();
    await flushPromises();

    // 折叠面板展开,显示 QuestionCard
    // (默认展开 awarded_score < full_score 的题;这道题全对 → 不默认展开)
    // 点开折叠项
    const collapse = wrapper.findAll('.el-collapse-item__header');
    expect(collapse.length).toBeGreaterThan(0);
    await collapse[0].trigger('click');
    await nextTick();

    // QuestionCard 渲染时,options 应透传 → 'A. 选项A内容' 出现
    const html = wrapper.html();
    expect(html).toContain('A.');
    expect(html).toContain('选项A内容');
    expect(html).toContain('B.');
    expect(html).toContain('选项B内容');
  });

  it('多选题:options 列表透传,所有选项 + 正确选项都展示', async () => {
    const multiOpts = ['甲选项', '乙选项', '丙选项', '丁选项'];
    const answer = makeAnswer({
      question_id: 2,
      sequence: 1,
      type: 'multi',
      options: multiOpts,
      user_answer: 'A,B',
      correct_answer: 'A,B',
      is_correct: true,
      awarded_score: 3,
      full_score: 3,
    });
    vi.mocked(apiMod.getResult).mockResolvedValue(makeResult([answer]));

    const router = makeRouter();
    router.push('/exam/1/result');
    await router.isReady();

    const wrapper = mount(ExamResult, {
      global: { plugins: [ElementPlus, router] },
    });
    await nextTick();
    await flushPromises();

    // 展开
    const collapse = wrapper.findAll('.el-collapse-item__header');
    await collapse[0].trigger('click');
    await nextTick();

    const html = wrapper.html();
    // 4 个选项全部出现
    for (const opt of multiOpts) {
      expect(html).toContain(opt);
    }
    // A/B/C/D 字母标记出现
    expect(html).toMatch(/A\./);
    expect(html).toMatch(/B\./);
  });

  it('计算题:options=null → 不渲染选项列表', async () => {
    const calcAnswer = makeAnswer({
      question_id: 3,
      type: 'calc',
      options: null,
      user_answer: '我的计算',
      correct_answer: '参考答案',
      is_correct: null,
      awarded_score: 0,
      full_score: 5,
    });
    vi.mocked(apiMod.getResult).mockResolvedValue(makeResult([calcAnswer]));

    const router = makeRouter();
    router.push('/exam/1/result');
    await router.isReady();

    const wrapper = mount(ExamResult, {
      global: { plugins: [ElementPlus, router] },
    });
    await nextTick();
    await flushPromises();

    // 错题默认展开(awarded_score=0 < full_score=5)
    await nextTick();
    const html = wrapper.html();
    // 不应包含 A./B. 选项标记(主观题无 options)
    // QuestionCard 对 calc 类型渲染 textarea,无 .options-list
    const optionsLists = wrapper.findAll('.options-list');
    expect(optionsLists.length).toBe(0);
  });

  it('判断题:options 非空 → QuestionCard 渲染 "对 / 错"', async () => {
    const judgeAnswer = makeAnswer({
      question_id: 4,
      type: 'judge',
      options: ['对', '错'],
      user_answer: '对',
      correct_answer: '对',
      is_correct: true,
      awarded_score: 1,
      full_score: 1,
    });
    vi.mocked(apiMod.getResult).mockResolvedValue(makeResult([judgeAnswer]));

    const router = makeRouter();
    router.push('/exam/1/result');
    await router.isReady();

    const wrapper = mount(ExamResult, {
      global: { plugins: [ElementPlus, router] },
    });
    await nextTick();
    await flushPromises();

    // 展开
    const collapse = wrapper.findAll('.el-collapse-item__header');
    if (collapse.length > 0) {
      await collapse[0].trigger('click');
      await nextTick();
    }

    const html = wrapper.html();
    expect(html).toContain('对');
    expect(html).toContain('错');
  });
});

describe('GradedAnswerDetail.options 类型契约 (fix-23a)', () => {
  it('type 字段包含 options (string[] | null | undefined)', () => {
    // 编译期保证:api.ts 的 GradedAnswerDetail 必须有 options 字段
    // runtime 检查:构造一个 GradedAnswerDetail 验证
    const a: GradedAnswerDetail = makeAnswer({ options: ['x', 'y'] });
    expect(a.options).toEqual(['x', 'y']);
  });

  it('options 可为 null (主观题)', () => {
    const a: GradedAnswerDetail = makeAnswer({ options: null });
    expect(a.options).toBeNull();
  });
});
