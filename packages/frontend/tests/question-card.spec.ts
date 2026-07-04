/**
 * 答题卡片切换测试 — 验证 exam store 中 goToQuestion 的行为。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import QuestionCard from '@/components/QuestionCard.vue';
import { useExamStore } from '@/stores/exam';
import type { QuestionPublic, StartExamResponse } from '@/types/api';

function makeFakeStart(): StartExamResponse {
  return {
    attempt_id: 100,
    started_at: new Date().toISOString(),
    time_limit_minutes: 120,
    total_score: 110,
    questions: [
      { id: 1, type: 'single', chapter_id: 1, chapter_code: 'ch1', difficulty: 1, stem: 'Q1', options: ['A', 'B'], score: 2, sequence: 1 },
      { id: 2, type: 'single', chapter_id: 1, chapter_code: 'ch1', difficulty: 1, stem: 'Q2', options: ['A', 'B'], score: 2, sequence: 2 },
      { id: 3, type: 'multi', chapter_id: 1, chapter_code: 'ch1', difficulty: 2, stem: 'Q3', options: ['A', 'B', 'C'], score: 3, sequence: 3 },
      { id: 4, type: 'calc', chapter_id: 1, chapter_code: 'ch1', difficulty: 2, stem: '计算题', options: null, score: 5, sequence: 4 },
    ],
  };
}

describe('答题卡片切换', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('applyStartResponse 后 currentSequence 默认 1', () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());
    expect(store.currentSequence).toBe(1);
    expect(store.currentQuestion?.id).toBe(1);
  });

  it('goToQuestion 切换到指定 sequence', () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());
    store.goToQuestion(2);
    expect(store.currentSequence).toBe(2);
    expect(store.currentQuestion?.id).toBe(2);
  });

  it('goToQuestion 越界不生效', () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());
    store.goToQuestion(99);
    expect(store.currentSequence).toBe(1);
    store.goToQuestion(0);
    expect(store.currentSequence).toBe(1);
  });

  it('setAnswer 写入选中答案', () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());
    store.setAnswer(1, 'A');
    expect(store.answers[1]).toBe('A');
    expect(store.answeredCount).toBe(1);
  });

  it('answeredCount 只统计非空答案', () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());
    store.setAnswer(1, 'A');
    store.setAnswer(2, '');
    expect(store.answeredCount).toBe(1);
  });

  it('按题型分组的题目数正确', () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());
    expect(store.questionsByType.single?.length).toBe(2);
    expect(store.questionsByType.multi?.length).toBe(1);
  });
});

describe('主观题答案输入（regression: el-input @input 不再传 Event）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('用户键入字符到 calc 题答案，store.answers[qid] 被正确设置', async () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());

    const calcQ: QuestionPublic = {
      id: 4,
      type: 'calc',
      chapter_id: 1,
      chapter_code: 'ch1',
      difficulty: 2,
      stem: '某项目初始投资 100 万，5 年内每年净收益 30 万，贴现率 10%。求 NPV。',
      options: null,
      score: 5,
      sequence: 4,
    };

    const wrapper = mount(QuestionCard, {
      props: { question: calcQ, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });

    // 触发 @update:model-value — Element Plus 传字符串（不是 Event）
    const textarea = wrapper.find('textarea');
    expect(textarea.exists()).toBe(true);
    await textarea.setValue('30 × (P/A,10%,5) - 100 = 13.74 万');

    // 等 Vue 同步
    await nextTick();

    // 应该收到一个 update:answer emit
    const events = wrapper.emitted('update:answer');
    expect(events).toBeTruthy();
    expect(events?.length).toBeGreaterThanOrEqual(1);
    const last = events![events!.length - 1];
    expect(last[0]).toBe(4); // question_id
    expect(last[1]).toBe('30 × (P/A,10%,5) - 100 = 13.74 万');
  });

  it('setAnswer 接受 undefined / null，归一为空串（undefined-safe）', () => {
    const store = useExamStore();
    store.applyStartResponse(makeFakeStart());
    // 不抛错
    expect(() => store.setAnswer(4, undefined as unknown as string)).not.toThrow();
    expect(store.answers[4]).toBe('');
    expect(() => store.setAnswer(4, null as unknown as string)).not.toThrow();
    expect(store.answers[4]).toBe('');
  });

  it('主观题初次渲染：userAnswer=undefined 时 textarea 不抛错', () => {
    const calcQ: QuestionPublic = {
      id: 5,
      type: 'calc',
      chapter_id: 1,
      chapter_code: 'ch1',
      difficulty: 2,
      stem: '计算题 2',
      options: null,
      score: 5,
      sequence: 5,
    };
    // 显式传 undefined — 模拟"未作答"
    const wrapper = mount(QuestionCard, {
      props: { question: calcQ, userAnswer: undefined as unknown as string },
      global: { plugins: [ElementPlus] },
    });
    expect(wrapper.exists()).toBe(true);
    // textarea 存在且 value 为空字符串
    const textarea = wrapper.find('textarea');
    expect(textarea.exists()).toBe(true);
    expect((textarea.element as HTMLTextAreaElement).value).toBe('');
  });
});

describe('主观题 placeholder 提示（fix-17）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('主观题 placeholder 提示按编号格式作答', () => {
    const calcQ: QuestionPublic = {
      id: 100,
      type: 'calc',
      chapter_id: 1,
      chapter_code: 'ch1',
      difficulty: 2,
      stem: '计算题',
      options: null,
      score: 5,
      sequence: 1,
    };
    const wrapper = mount(QuestionCard, {
      props: { question: calcQ, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const textarea = wrapper.find('textarea');
    expect(textarea.exists()).toBe(true);
    const placeholder = textarea.attributes('placeholder') ?? '';
    expect(placeholder).toContain('请按');
    expect(placeholder).toContain('1.xxx；2.xxx');
  });

  it('综合题 placeholder 同样提示按编号格式作答', () => {
    const compQ: QuestionPublic = {
      id: 101,
      type: 'comprehensive',
      chapter_id: 1,
      chapter_code: 'ch1',
      difficulty: 3,
      stem: '综合题',
      options: null,
      score: 10,
      sequence: 1,
    };
    const wrapper = mount(QuestionCard, {
      props: { question: compQ, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const textarea = wrapper.find('textarea');
    expect(textarea.exists()).toBe(true);
    expect(textarea.attributes('placeholder')).toContain('1.xxx；2.xxx');
  });
});
