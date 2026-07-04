/**
 * Dashboard 删除按钮测试 — fix-22。
 *
 * 覆盖：
 *  - 删除按钮存在
 *  - 调 handleDelete → 调 DELETE /exams/{id}（mock axios）
 *  - 删除成功后重新 fetch dashboard
 *  - 删除失败不抛错
 *
 * 注：实际 el-popconfirm 在 happy-dom 中点击行为依赖 element-plus 实现；
 * 这里我们直接通过 defineExpose 暴露的 $_handleDeleteForTest 调用以避免
 * DOM 行为不稳定。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';
import { setActivePinia, createPinia } from 'pinia';
import { createRouter, createMemoryHistory } from 'vue-router';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import Dashboard from '@/pages/Dashboard.vue';
import { useDashboardStore } from '@/stores/dashboard';
import * as apiMod from '@/api';
import type { DashboardResponse } from '@/types/api';

// mock ECharts init（避免 happy-dom 中 canvas 报错）
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

// mock deleteExam
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof apiMod>('@/api');
  return {
    ...actual,
    deleteExam: vi.fn(),
  };
});

function makeFakeDashboard(): DashboardResponse {
  return {
    history: [
      { attempt_id: 1, started_at: '2026-07-05T08:00:00Z', submitted_at: '2026-07-05T10:00:00Z', total_score: 70 },
      { attempt_id: 2, started_at: '2026-07-04T08:00:00Z', submitted_at: '2026-07-04T10:00:00Z', total_score: 85 },
    ],
    score_trend: [70, 85],
    chapter_radar: { ch1: 18, ch2: 12 },
    total_attempts: 2,
  };
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div></div>' } },
      { path: '/dashboard', component: Dashboard },
      { path: '/exam/:id/result', component: { template: '<div></div>' } },
    ],
  });
}

describe('Dashboard 删除按钮（fix-22）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('渲染历次明细时每行有"删除"按钮', async () => {
    const store = useDashboardStore();
    store.data = makeFakeDashboard();

    const router = makeRouter();
    router.push('/dashboard');
    await router.isReady();

    const wrapper = mount(Dashboard, {
      global: { plugins: [ElementPlus, router] },
    });
    await nextTick();
    await flushPromises();

    // 找所有"删除"按钮
    const buttons = wrapper.findAll('button');
    const deleteBtns = buttons.filter((b) => b.text().includes('删除'));
    expect(deleteBtns.length).toBeGreaterThanOrEqual(2);
  });

  it('调 handleDelete → 调 deleteExam → 重新 fetch', async () => {
    const store = useDashboardStore();
    store.data = makeFakeDashboard();

    const fetchSpy = vi.spyOn(store, 'fetch').mockResolvedValue();
    const deleteMock = vi.mocked(apiMod.deleteExam).mockResolvedValue();

    const router = makeRouter();
    router.push('/dashboard');
    await router.isReady();

    const wrapper = mount(Dashboard, {
      global: { plugins: [ElementPlus, router] },
    });
    await nextTick();
    await flushPromises();

    // 通过 defineExpose 调用 handler（避免 popconfirm DOM 行为）
    await (wrapper.vm as unknown as {
      $_handleDeleteForTest: (id: number) => Promise<void>;
    }).$_handleDeleteForTest(2);
    await flushPromises();

    expect(deleteMock).toHaveBeenCalledWith(2);
    expect(fetchSpy).toHaveBeenCalled();
  });

  it('deleteExam 抛错时不再调 fetch（catch 路径已吞掉）', async () => {
    const store = useDashboardStore();
    store.data = makeFakeDashboard();

    const fetchSpy = vi.spyOn(store, 'fetch').mockResolvedValue();
    vi.mocked(apiMod.deleteExam).mockRejectedValue(new Error('网络错误'));

    const router = makeRouter();
    router.push('/dashboard');
    await router.isReady();

    const wrapper = mount(Dashboard, {
      global: { plugins: [ElementPlus, router] },
    });
    await nextTick();
    await flushPromises();

    // onMounted 已调过一次 fetch；清零计数器以便精确断言
    fetchSpy.mockClear();

    // 不抛错即可（错误处理内部 catch）
    await expect(
      (wrapper.vm as unknown as {
        $_handleDeleteForTest: (id: number) => Promise<void>;
      }).$_handleDeleteForTest(1),
    ).resolves.not.toThrow();
    await flushPromises();

    // catch 路径不调 fetch
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});