/**
 * AI 讲解面板测试 — 验证真实 SSE 流式渲染。
 *
 * 策略：
 *  - mock `@/api`，用可控的 async generator 模拟 SSE 事件序列
 *  - 三个核心场景：① start → delta 累积 → end 解析；② end 时 available=false 走 fallback；
 *    ③ HTTP 抛错时显示错误态
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';

/**
 * 用 vi.hoisted 让 mock 工厂能拿到 spy 引用（vi.mock 会被 hoist 到顶部，
 * 此时模块顶层声明的 vi.fn() 还没初始化 → 必须用 hoisted 才能闭包共享）。
 */
const { streamMock, explainMock } = vi.hoisted(() => ({
  streamMock: vi.fn(),
  explainMock: vi.fn(),
}));

/** 可控 SSE 事件源 — 测试时按顺序 push 事件让 generator yield。 */
function makeStream(events: Record<string, unknown>[]) {
  return (async function* () {
    for (const ev of events) yield ev;
  })();
}

vi.mock('@/api', () => ({
  explainQuestionStream: (attemptId: number, questionId: number, level: string) =>
    streamMock(attemptId, questionId, level),
  explainQuestion: (...args: unknown[]) => explainMock(...args),
}));

const { default: ExplainPanel } = await import('@/components/ExplainPanel.vue');

function mountPanel(props: { visible: boolean; attemptId: number; questionId: number }) {
  return mount(ExplainPanel, {
    props,
    global: { plugins: [ElementPlus] },
  });
}

describe('AI 讲解面板 — 真实 SSE 流式渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    explainMock.mockResolvedValue({
      question_id: 99,
      available: false,
      explanation: '讲解暂不可用（占位 stub）',
      reference_answer: 'B',
      analysis: '题库官方解析',
    });
    // 默认 stub：一次正常端到端流
    streamMock.mockImplementation(() =>
      makeStream([
        { done: false, event: 'start', question_id: 99 },
        { done: false, event: 'delta', delta: '{\n  "available": true,\n  "summary": "考察' },
        { done: false, event: 'delta', delta: '优序融资理论",\n  "explanation": "内部融资优先",\n' },
        { done: false, event: 'delta', delta: '  "key_points": ["要点A", "要点B"],\n' },
        { done: false, event: 'delta', delta: '  "common_pitfalls": ["易错X"]\n}' },
        { done: true, available: true, question_id: 99, reference_answer: 'B', analysis: '官方解析' },
      ]),
    );
  });

  it('visible=true 时自动调 explainQuestionStream', async () => {
    mountPanel({ visible: true, attemptId: 1, questionId: 99 });
    await flushPromises();
    expect(streamMock).toHaveBeenCalledWith(1, 99, 'standard');
  });

  it('visible=false 时不调流', async () => {
    mountPanel({ visible: false, attemptId: 1, questionId: 99 });
    await flushPromises();
    expect(streamMock).not.toHaveBeenCalled();
  });

  it('流式累积 streamedText', async () => {
    const wrapper = mountPanel({ visible: true, attemptId: 1, questionId: 99 });
    await flushPromises();
    await nextTick();
    const vm = wrapper.vm as unknown as { streamedText: string };
    expect(vm.streamedText).toContain('"summary"');
    expect(vm.streamedText).toContain('优序融资理论');
  });

  it('end 事件后解析 LLM JSON 填充结构化字段', async () => {
    const wrapper = mountPanel({ visible: true, attemptId: 1, questionId: 99 });
    await flushPromises();
    await nextTick();
    const vm = wrapper.vm as unknown as {
      title: string;
      explanation: string;
      missedPoints: string[];
      studyTip: string;
      fallbackAnswer: string;
      fallbackAnalysis: string;
      streaming: boolean;
    };
    expect(vm.title).toBe('考察优序融资理论');
    expect(vm.explanation).toBe('内部融资优先');
    expect(vm.missedPoints).toEqual(['要点A', '要点B']);
    expect(vm.studyTip).toBe('易错X');
    expect(vm.fallbackAnswer).toBe('B');
    expect(vm.fallbackAnalysis).toBe('官方解析');
    expect(vm.streaming).toBe(false);
  });

  it('end 事件 available=false 时走 fallback 展示', async () => {
    streamMock.mockImplementationOnce(() =>
      makeStream([
        { done: false, event: 'start', question_id: 99 },
        { done: true, available: false, question_id: 99, reference_answer: 'A', analysis: '兜底解析' },
      ]),
    );
    const wrapper = mountPanel({ visible: true, attemptId: 1, questionId: 99 });
    await flushPromises();
    await nextTick();
    const vm = wrapper.vm as unknown as {
      fallback: boolean;
      title: string;
      fallbackAnswer: string;
      fallbackAnalysis: string;
    };
    expect(vm.fallback).toBe(true);
    expect(vm.title).toBe('讲解暂不可用');
    expect(vm.fallbackAnswer).toBe('A');
    expect(vm.fallbackAnalysis).toBe('兜底解析');
    // fallback 走完后会再调一次 explainQuestion 拿兜底（mock 返回的也是 A/题库官方解析）
    expect(explainMock).toHaveBeenCalled();
  });

  it('流式抛错时进入错误态 + 兜底', async () => {
    streamMock.mockImplementationOnce(() => {
      throw new Error('SSE HTTP 500');
    });
    const wrapper = mountPanel({ visible: true, attemptId: 1, questionId: 99 });
    await flushPromises();
    await nextTick();
    const vm = wrapper.vm as unknown as {
      fallback: boolean;
      title: string;
      streaming: boolean;
      fallbackAnswer: string;
    };
    expect(vm.fallback).toBe(true);
    expect(vm.title).toBe('讲解暂不可用');
    expect(vm.streaming).toBe(false);
    expect(vm.fallbackAnswer).toBe('B'); // 来自 explainQuestion stub
  });
});