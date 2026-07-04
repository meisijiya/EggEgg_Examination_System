/**
 * 仪表盘 store — 历次成绩 + 趋势 + 章节雷达。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { getDashboard } from '@/api';
import type { DashboardResponse } from '@/types/api';

export const useDashboardStore = defineStore('dashboard', () => {
  // ---------- state ----------
  const data = ref<DashboardResponse | null>(null);
  const loading = ref<boolean>(false);
  const error = ref<string>('');

  // ---------- actions ----------

  /**
   * 拉取仪表盘数据（GET /dashboard）。
   */
  async function fetch(): Promise<void> {
    loading.value = true;
    error.value = '';
    try {
      data.value = await getDashboard();
    } catch (e) {
      error.value = (e as { message?: string })?.message ?? '加载失败';
    } finally {
      loading.value = false;
    }
  }

  /**
   * 清空（用于登出）。
   */
  function reset(): void {
    data.value = null;
    loading.value = false;
    error.value = '';
  }

  return { data, loading, error, fetch, reset };
});
