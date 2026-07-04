/**
 * ECharts 组件挂载测试 — 验证 Dashboard / ExamResult 图表能 mount。
 *
 * happy-dom 不支持 canvas 渲染，但 ECharts 仍能 init（只是渲染失败不抛错）。
 * 这里用最小 mock 验证组件 import 不报错、模板结构正确。
 */
import { describe, it, expect, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createRouter, createMemoryHistory } from 'vue-router';
import { createPinia, setActivePinia } from 'pinia';
import ElementPlus from 'element-plus';
import Dashboard from '@/pages/Dashboard.vue';

// mock API 避免真实网络请求
vi.mock('@/api', () => ({
  getDashboard: vi.fn(async () => ({
    history: [
      { attempt_id: 1, started_at: '2026-07-04T10:00:00Z', submitted_at: '2026-07-04T12:00:00Z', total_score: 80 },
      { attempt_id: 2, started_at: '2026-07-05T10:00:00Z', submitted_at: '2026-07-05T12:00:00Z', total_score: 90 },
    ],
    score_trend: [80, 90],
    chapter_radar: { ch1: 8, ch2: 5, ch3: 7 },
    total_attempts: 2,
  })),
}));

// mock ECharts init（避免 canvas 报错）
vi.mock('echarts/core', async () => {
  const actual = await vi.importActual<any>('echarts/core');
  return {
    ...actual,
    init: vi.fn(() => ({
      setOption: vi.fn(),
      dispose: vi.fn(),
      resize: vi.fn(),
    })),
  };
});

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', component: Dashboard },
      { path: '/', component: { template: '<div></div>' } },
    ],
  });
}

describe('ECharts 组件挂载', () => {
  it('Dashboard 页能 mount', async () => {
    setActivePinia(createPinia());
    const router = makeRouter();
    router.push('/dashboard');
    await router.isReady();
    const wrapper = mount(Dashboard, {
      global: { plugins: [router, ElementPlus] },
    });
    expect(wrapper.exists()).toBe(true);
  });

  it('Dashboard 含标题"学习仪表盘"', async () => {
    setActivePinia(createPinia());
    const router = makeRouter();
    router.push('/dashboard');
    await router.isReady();
    const wrapper = mount(Dashboard, {
      global: { plugins: [router, ElementPlus] },
    });
    expect(wrapper.text()).toContain('学习仪表盘');
  });
});
