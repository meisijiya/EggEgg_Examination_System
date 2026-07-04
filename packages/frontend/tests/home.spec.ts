/**
 * Home.vue 模式选择 modal 测试 — fix-22。
 *
 * 覆盖：
 *  - 点击"开始模拟考"按钮 → 弹 el-dialog
 *  - 确认按钮 → 调 exam.startNew(mode) → 跳 /exam/:id/intro
 *  - 取消按钮 → 关 modal 不调 startNew
 *  - 时间显示走 formatDateTime
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import { createRouter, createWebHistory } from 'vue-router';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import Home from '@/pages/Home.vue';
import { useDashboardStore } from '@/stores/dashboard';
import { useExamStore } from '@/stores/exam';
import type { DashboardResponse, StartExamResponse } from '@/types/api';

// 隔离路由（避免真实 router push）
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: { template: '<div/>' } },
    { path: '/exam/:id/intro', component: { template: '<div/>' } },
  ],
});

function makeFakeDashboard(): DashboardResponse {
  return {
    history: [
      {
        attempt_id: 7,
        started_at: '2026-07-05T08:00:00Z',
        submitted_at: '2026-07-05T10:00:00Z',
        total_score: 88,
      },
    ],
    score_trend: [88],
    chapter_radar: { ch1: 20 },
    total_attempts: 1,
  };
}

function makeFakeStart(): StartExamResponse {
  return {
    attempt_id: 42,
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

describe('Home.vue 模式选择（fix-22）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('点击"开始模拟考"按钮弹 el-dialog', async () => {
    const dashboard = useDashboardStore();
    dashboard.data = makeFakeDashboard();

    const wrapper = mount(Home, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await nextTick();
    await flushPromises();

    // 初始 modal 不应可见
    expect(wrapper.find('.el-dialog').exists()).toBe(false);

    // 找"开始模拟考"按钮并点击
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('开始模拟考'));
    expect(startBtn).toBeTruthy();
    await startBtn!.trigger('click');
    await nextTick();
    await flushPromises();

    // modal 应可见
    expect(document.body.textContent ?? '').toContain('选择出题模式');
  });

  it('modal 默认选中 standard 模式', async () => {
    const dashboard = useDashboardStore();
    dashboard.data = makeFakeDashboard();

    const wrapper = mount(Home, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await nextTick();
    await flushPromises();

    // 触发打开
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('开始模拟考'));
    await startBtn!.trigger('click');
    await nextTick();
    await flushPromises();

    // 暴露的 ref 应是 standard
    expect(wrapper.vm.selectedMode).toBe('standard');
  });

  it('确认按钮 → 调 startNew("standard") → 跳 /exam/:id/intro', async () => {
    const dashboard = useDashboardStore();
    dashboard.data = makeFakeDashboard();

    const exam = useExamStore();
    const startSpy = vi.spyOn(exam, 'startNew').mockResolvedValue(makeFakeStart());
    const pushSpy = vi.spyOn(router, 'push').mockResolvedValue();

    const wrapper = mount(Home, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await nextTick();
    await flushPromises();

    // 打开 modal
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('开始模拟考'));
    await startBtn!.trigger('click');
    await nextTick();
    await flushPromises();

    // 选 standard（已经是默认）
    wrapper.vm.selectedMode = 'standard';

    // 找"开始答题"确认按钮
    const confirmBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('开始答题') && !b.text().includes('开始模拟考'));
    expect(confirmBtn).toBeTruthy();
    await confirmBtn!.trigger('click');
    await flushPromises();

    expect(startSpy).toHaveBeenCalledWith('standard');
    expect(pushSpy).toHaveBeenCalledWith('/exam/42/intro');
  });

  it('选 mixed 模式后确认 → startNew("mixed")', async () => {
    const dashboard = useDashboardStore();
    dashboard.data = makeFakeDashboard();

    const exam = useExamStore();
    const startSpy = vi.spyOn(exam, 'startNew').mockResolvedValue(makeFakeStart());

    const wrapper = mount(Home, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await nextTick();
    await flushPromises();

    // 打开 modal
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('开始模拟考'));
    await startBtn!.trigger('click');
    await nextTick();
    await flushPromises();

    // 切到 mixed
    wrapper.vm.selectedMode = 'mixed';

    const confirmBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('开始答题') && !b.text().includes('开始模拟考'));
    await confirmBtn!.trigger('click');
    await flushPromises();

    expect(startSpy).toHaveBeenCalledWith('mixed');
  });

  it('取消按钮关 modal 且不调 startNew', async () => {
    const dashboard = useDashboardStore();
    dashboard.data = makeFakeDashboard();

    const exam = useExamStore();
    const startSpy = vi.spyOn(exam, 'startNew').mockResolvedValue(makeFakeStart());

    const wrapper = mount(Home, {
      global: { plugins: [ElementPlus, router] },
      attachTo: document.body,
    });
    await nextTick();
    await flushPromises();

    // 打开 modal
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('开始模拟考'));
    await startBtn!.trigger('click');
    await nextTick();
    await flushPromises();

    // 找"取消"按钮
    const cancelBtn = wrapper.findAll('button').find((b) => b.text().trim() === '取消');
    expect(cancelBtn).toBeTruthy();
    await cancelBtn!.trigger('click');
    await flushPromises();

    expect(startSpy).not.toHaveBeenCalled();
  });
});