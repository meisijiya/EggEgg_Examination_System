/**
 * 7 题型渲染测试 — fix-30b (含 5 旧题型向后兼容 + 2 新题型 short_answer / case_analysis)。
 *
 * 覆盖:
 *  - 5 旧题型(单选/多选/判断/计算/综合)仍正常渲染
 *  - 简答题:textarea + 字数限制 + 关键点提示
 *  - 案例分析:多子问题 textarea + conclusion textarea
 *  - 用户输入触发 emit update:answer(含 JSON 序列化)
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import QuestionCard from '@/components/QuestionCard.vue';
import type { QuestionPublic, QuestionRubric } from '@/types/api';

function makeQuestion(overrides: Partial<QuestionPublic>): QuestionPublic {
  return {
    id: 1,
    type: 'single',
    chapter_id: 1,
    chapter_code: 'ch1',
    difficulty: 1,
    stem: 'Q stem',
    options: ['A', 'B'],
    score: 2,
    sequence: 1,
    ...overrides,
  };
}

describe('7 题型渲染(fix-30b)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  // ---------- 5 旧题型向后兼容 ----------

  it('单选题渲染:options 列表', () => {
    const q = makeQuestion({ type: 'single', options: ['选项1', '选项2'] });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    expect(w.find('.options-list').exists()).toBe(true);
    expect(w.text()).toContain('A.');
    expect(w.text()).toContain('选项1');
  });

  it('多选题渲染:选项点击 toggle', async () => {
    const q = makeQuestion({ type: 'multi', options: ['甲', '乙', '丙'] });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const opts = w.findAll('.answer-option');
    expect(opts.length).toBe(3);
    await opts[0].trigger('click');
    await nextTick();
    const events = w.emitted('update:answer');
    expect(events).toBeTruthy();
    expect(events![0][1]).toBe('A');
  });

  it('判断题渲染:对/错选项', () => {
    const q = makeQuestion({ type: 'judge', options: null });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '对' },
      global: { plugins: [ElementPlus] },
    });
    expect(w.text()).toContain('对');
    expect(w.text()).toContain('错');
  });

  it('计算题渲染:主观题 textarea + placeholder', () => {
    const q = makeQuestion({ type: 'calc', options: null });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const ta = w.find('textarea');
    expect(ta.exists()).toBe(true);
    expect(ta.attributes('placeholder')).toContain('1.xxx');
  });

  it('综合题渲染:主观题 textarea', () => {
    const q = makeQuestion({ type: 'comprehensive', options: null });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    expect(w.find('textarea').exists()).toBe(true);
  });

  // ---------- 2 新题型(fix-30b) ----------

  it('简答题:textarea + show-word-limit + 关键点提示', () => {
    const rubric: QuestionRubric = {
      sub_questions: [{ id: 'sq1', points: 5, key_points: ['要点1', '要点2'], weight: 1 }],
      conclusion: { points: 0, criteria: [] },
    };
    const q = makeQuestion({ type: 'short_answer', options: null, rubric });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const ta = w.find('[data-testid="short-answer-input"] textarea');
    expect(ta.exists()).toBe(true);
    expect(ta.attributes('maxlength')).toBe('1000');
    expect(w.text()).toContain('要点1');
    expect(w.text()).toContain('要点2');
  });

  it('简答题:用户输入 emit update:answer 为字符串(非 JSON)', async () => {
    const q = makeQuestion({ type: 'short_answer', options: null });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const ta = w.find('[data-testid="short-answer-input"] textarea');
    await ta.setValue('我的答案');
    await nextTick();
    const events = w.emitted('update:answer');
    expect(events).toBeTruthy();
    const last = events![events!.length - 1];
    expect(last[1]).toBe('我的答案');
  });

  it('案例分析:多子问题 textarea + conclusion textarea', () => {
    const rubric: QuestionRubric = {
      sub_questions: [
        { id: '1', points: 6, key_points: ['要点A', '要点B'], weight: 0.6 },
        { id: '2', points: 4, key_points: ['要点C'], weight: 0.4 },
      ],
      conclusion: { points: 2, criteria: ['结论清晰'] },
    };
    const q = makeQuestion({ type: 'case_analysis', options: null, rubric });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    expect(w.find('[data-testid="case-sub-1"]').exists()).toBe(true);
    expect(w.find('[data-testid="case-sub-2"]').exists()).toBe(true);
    expect(w.find('[data-testid="case-conclusion"]').exists()).toBe(true);
    expect(w.text()).toContain('子问题 1');
    expect(w.text()).toContain('(6 分)');
    expect(w.text()).toContain('结论');
  });

  it('案例分析:子问题输入 → emit JSON 字符串', async () => {
    const rubric: QuestionRubric = {
      sub_questions: [{ id: '1', points: 6, key_points: [], weight: 1 }],
      conclusion: { points: 2, criteria: [] },
    };
    const q = makeQuestion({ type: 'case_analysis', options: null, rubric });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const ta = w.find('[data-testid="case-sub-1"] textarea');
    expect(ta.exists()).toBe(true);
    await ta.setValue('子问题1答案');
    await nextTick();
    const events = w.emitted('update:answer');
    expect(events).toBeTruthy();
    const last = events![events!.length - 1];
    expect(last[0]).toBe(1);
    const parsed = JSON.parse(last[1] as string);
    expect(parsed.sub_answers['1']).toBe('子问题1答案');
    expect(parsed.conclusion).toBe('');
  });

  it('案例分析:conclusion 输入 → emit JSON 含 conclusion', async () => {
    const rubric: QuestionRubric = {
      sub_questions: [{ id: '1', points: 6, key_points: [], weight: 1 }],
      conclusion: { points: 2, criteria: [] },
    };
    const q = makeQuestion({ type: 'case_analysis', options: null, rubric });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    const conc = w.find('[data-testid="case-conclusion"] textarea');
    expect(conc.exists()).toBe(true);
    await conc.setValue('总结论内容');
    await nextTick();
    const events = w.emitted('update:answer');
    const parsed = JSON.parse(events![events!.length - 1][1] as string);
    expect(parsed.conclusion).toBe('总结论内容');
  });

  it('案例分析:历史脏数据(非 JSON)兜底为空白', () => {
    const rubric: QuestionRubric = {
      sub_questions: [{ id: '1', points: 6, key_points: [], weight: 1 }],
      conclusion: { points: 2, criteria: [] },
    };
    const q = makeQuestion({ type: 'case_analysis', options: null, rubric });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: 'plain text not json' },
      global: { plugins: [ElementPlus] },
    });
    // 不抛错即可
    expect(w.exists()).toBe(true);
  });

  it('案例分析:多子问题独立答案 — 互不覆盖', async () => {
    const rubric: QuestionRubric = {
      sub_questions: [
        { id: 'A', points: 5, key_points: [], weight: 0.5 },
        { id: 'B', points: 5, key_points: [], weight: 0.5 },
      ],
      conclusion: { points: 0, criteria: [] },
    };
    const q = makeQuestion({ type: 'case_analysis', options: null, rubric });
    const w = mount(QuestionCard, {
      props: { question: q, userAnswer: '' },
      global: { plugins: [ElementPlus] },
    });
    // 模拟 store 反馈:每次 emit 后更新 props.userAnswer(模拟 setAnswer)
    w.find('[data-testid="case-sub-A"] textarea').setValue('A答案');
    await nextTick();
    // 用最近一次 emit 的 JSON 作为下次 input 的 userAnswer
    const events1 = w.emitted('update:answer');
    const json1 = events1![events1!.length - 1][1] as string;
    await w.setProps({ userAnswer: json1 });
    await nextTick();
    w.find('[data-testid="case-sub-B"] textarea').setValue('B答案');
    await nextTick();
    const events2 = w.emitted('update:answer');
    const json2 = events2![events2!.length - 1][1] as string;
    const parsed = JSON.parse(json2);
    expect(parsed.sub_answers['A']).toBe('A答案');
    expect(parsed.sub_answers['B']).toBe('B答案');
  });
});