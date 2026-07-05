/**
 * Admin.vue AI 生成题 review queue 交互测试 — fix-30a。
 *
 * 覆盖:
 *  - 默认 activeTab = 'review'(原 review queue)
 *  - 切到 ai-generated tab → 调 getAiGeneratedQuestions
 *  - approve 按钮 → 调 approveQuestion + 从列表移除
 *  - reject 按钮 → 弹 ElMessageBox.prompt 输入原因 + 调 rejectQuestion
 *  - source_ref 折叠栏默认收起(el-table expand-row-keys = [])
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import { createRouter, createWebHistory } from 'vue-router';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import Admin from '@/pages/Admin.vue';
import * as subjectsApi from '@/api/subjects';
import { useAuthStore } from '@/stores/auth';
import { TOKEN_KEY, ROLE_KEY } from '@/api/client';
import type { AiGeneratedQuestionsResponse, AiGeneratedQuestion } from '@/types/api';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: { template: '<div/>' } },
    { path: '/login', component: { template: '<div/>' } },
    { path: '/admin', component: Admin },
  ],
});

function makeFakeAiList(): AiGeneratedQuestionsResponse {
  const items: AiGeneratedQuestion[] = [
    {
      id: 100,
      subject_id: 'fin-mgmt',
      type: 'single',
      chapter_code: 'ch1',
      difficulty: 2,
      stem: 'AI 生成的题 1',
      options: ['A', 'B'],
      generated_answer: 'A',
      key_points: ['要点1'],
      confidence: 0.85,
      source_ref: {
        file: 'ch1.docx',
        paragraph_index: 12,
        snippet: '引用原文段落',
      },
      agent_trace: [{ agent: 'generator', output: '初稿' }],
    },
    {
      id: 101,
      subject_id: 'corp-strat',
      type: 'calc',
      chapter_code: 'ch3',
      difficulty: 3,
      stem: 'AI 生成的计算题',
      options: null,
      generated_answer: '解析...',
      key_points: ['公式'],
      confidence: 0.65,
      source_ref: {
        file: 'strategy.pdf',
        paragraph_index: 5,
        snippet: '引用段落',
      },
    },
  ];
  return { items, total: items.length };
}

describe('Admin AI 生成题 review(fix-30a)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.restoreAllMocks();
    // 模拟 admin 已登录
    localStorage.setItem(TOKEN_KEY, 'admin-token');
    localStorage.setItem(ROLE_KEY, 'admin');
  });

  it('默认显示原 review tab', async () => {
    const w = mount(Admin, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await flushPromises();
    // tab pane 应有 'review' 与 'ai-generated'
    const tabs = w.findAll('.el-tabs__item');
    expect(tabs.length).toBeGreaterThanOrEqual(2);
    expect(w.text()).toContain('原题库 review');
    expect(w.text()).toContain('AI 生成题 review');
  });

  it('切到 ai-generated tab 调 getAiGeneratedQuestions', async () => {
    const spy = vi
      .spyOn(subjectsApi, 'getAiGeneratedQuestions')
      .mockResolvedValue(makeFakeAiList());
    const w = mount(Admin, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await flushPromises();
    // 通过 wrapper.vm.onTabChange 模拟 el-tabs tab-change 事件
    w.vm.onTabChange('ai-generated');
    await flushPromises();
    expect(spy).toHaveBeenCalled();
  });

  it('approve 按钮 → 调 approveQuestion + 从列表移除', async () => {
    vi.spyOn(subjectsApi, 'getAiGeneratedQuestions').mockResolvedValue(makeFakeAiList());
    const approveSpy = vi
      .spyOn(subjectsApi, 'approveQuestion')
      .mockResolvedValue(undefined);
    const w = mount(Admin, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await flushPromises();
    w.vm.onTabChange('ai-generated');
    await flushPromises();
    await w.vm.onApproveAi(100);
    await flushPromises();
    expect(approveSpy).toHaveBeenCalledWith(100);
    expect(w.vm.aiQuestions.find((q: { id: number }) => q.id === 100)).toBeUndefined();
    expect(w.vm.aiQuestions.find((q: { id: number }) => q.id === 101)).toBeTruthy();
  });

  it('reject 按钮 → 弹 prompt → 调 rejectQuestion(reason)', async () => {
    vi.spyOn(subjectsApi, 'getAiGeneratedQuestions').mockResolvedValue(makeFakeAiList());
    const rejectSpy = vi
      .spyOn(subjectsApi, 'rejectQuestion')
      .mockResolvedValue(undefined);
    const w = mount(Admin, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await flushPromises();
    w.vm.onTabChange('ai-generated');
    await flushPromises();
    // 模拟 ElMessageBox.prompt 返回 reason
    const ElMessageBoxMod = await import('element-plus');
    vi.spyOn(ElMessageBoxMod.ElMessageBox, 'prompt').mockResolvedValue({
      value: '题目重复,不要入库',
      action: 'confirm' as const,
    } as Awaited<ReturnType<typeof ElMessageBoxMod.ElMessageBox.prompt>>);
    await w.vm.onRejectAi(101);
    await flushPromises();
    expect(rejectSpy).toHaveBeenCalledWith(101, '题目重复,不要入库');
    expect(w.vm.aiQuestions.find((q: { id: number }) => q.id === 101)).toBeUndefined();
  });

  it('source_ref 默认收起(用户硬约束)', async () => {
    vi.spyOn(subjectsApi, 'getAiGeneratedQuestions').mockResolvedValue(makeFakeAiList());
    const w = mount(Admin, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await flushPromises();
    w.vm.onTabChange('ai-generated');
    await flushPromises();
    // expandedAiRows 应为空
    expect(w.vm.expandedAiRows).toEqual([]);
  });

  it('approve 失败 → 保留列表项(不删除)', async () => {
    vi.spyOn(subjectsApi, 'getAiGeneratedQuestions').mockResolvedValue(makeFakeAiList());
    vi.spyOn(subjectsApi, 'approveQuestion').mockRejectedValue(
      new Error('Network error'),
    );
    const w = mount(Admin, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await flushPromises();
    w.vm.onTabChange('ai-generated');
    await flushPromises();
    await w.vm.onApproveAi(100);
    await flushPromises();
    expect(w.vm.aiQuestions.find((q: { id: number }) => q.id === 100)).toBeTruthy();
  });
});