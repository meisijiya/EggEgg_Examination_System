<script setup lang="ts">
/**
 * /dashboard 仪表盘 — 历次成绩 + ECharts 折线 + 雷达 + 列表。
 *
 * fix-22 改造：
 * - 历次明细表加 "删除" 按钮（el-popconfirm 二次确认 → DELETE /exams/{id}）
 * - 时间展示统一走 formatDateTime（强制 Shanghai 时区）
 */
import { computed, defineExpose, onMounted, onBeforeUnmount, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import * as echarts from 'echarts/core';
import { LineChart, RadarChart } from 'echarts/charts';
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  PolarComponent,
  DataZoomComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useDashboardStore } from '@/stores/dashboard';
import { formatChapterCode } from '@/utils/formatChapterCode';
import { deleteExam } from '@/api';
import { formatDateTime, formatDate } from '@/utils/format';

echarts.use([
  LineChart,
  RadarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  PolarComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

const router = useRouter();
const dashboard = useDashboardStore();

const trendEl = ref<HTMLDivElement | null>(null);
const radarEl = ref<HTMLDivElement | null>(null);
let trendChart: echarts.ECharts | null = null;
let radarChart: echarts.ECharts | null = null;

onMounted(async () => {
  await dashboard.fetch();
  if (dashboard.data) {
    requestAnimationFrame(() => {
      initCharts();
    });
  }
});

onBeforeUnmount(() => {
  trendChart?.dispose();
  radarChart?.dispose();
  trendChart = null;
  radarChart = null;
});

/** 历次成绩折线图。 */
const trendOption = computed(() => {
  const data = dashboard.data;
  if (!data) return {};
  const xs = data.history.map((h) => formatDate(h.started_at));
  return {
    title: { text: '历次总分趋势', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 40, bottom: 50 },
    xAxis: { type: 'category', data: xs, axisLabel: { rotate: 30 } },
    yAxis: { type: 'value', name: '分', max: 110 },
    series: [
      {
        name: '总分',
        type: 'line',
        data: data.score_trend,
        smooth: true,
        lineStyle: { color: '#5BA0E2', width: 3 },     /* chart-1 sky */
        itemStyle: { color: '#5BA0E2' },
        areaStyle: { color: 'rgba(91, 160, 226, 0.18)' },
        symbol: 'circle',
        symbolSize: 8,
      },
    ],
  };
});

/** 章节雷达图。 */
const radarOption = computed(() => {
  const data = dashboard.data;
  if (!data) return {};
  const codes = Object.keys(data.chapter_radar).sort();
  const values = codes.map((c) => data.chapter_radar[c] ?? 0);
  return {
    title: { text: '章节平均得分', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: {},
    radar: {
      indicator: codes.map((c) => ({ name: formatChapterCode(c), max: 25 })),
      radius: '65%',
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: values,
            name: '平均得分',
            areaStyle: { color: 'rgba(88, 194, 158, 0.25)' },
            lineStyle: { color: '#58C29E' },    /* chart-3 mint */
            itemStyle: { color: '#58C29E' },
          },
        ],
      },
    ],
  };
});

function initCharts(): void {
  if (trendEl.value) {
    trendChart = echarts.init(trendEl.value);
    trendChart.setOption(trendOption.value);
  }
  if (radarEl.value) {
    radarChart = echarts.init(radarEl.value);
    radarChart.setOption(radarOption.value);
  }
}

/** 历次平均分。 */
const avgScore = computed(() => {
  const t = dashboard.data?.score_trend ?? [];
  if (t.length === 0) return 0;
  const s = t.reduce((a, b) => a + b, 0);
  return Math.round((s / t.length) * 10) / 10;
});

/** 历次最高分。 */
const maxScore = computed(() => {
  return Math.max(0, ...(dashboard.data?.score_trend ?? []));
});

/** 趋势（最近 vs 上次）。 */
const trendDelta = computed(() => {
  const t = dashboard.data?.score_trend ?? [];
  if (t.length < 2) return 0;
  return Math.round((t[t.length - 1] - t[t.length - 2]) * 10) / 10;
});

/** 删除中标记 — 防止同一行重复点击。 */
const deletingId = ref<number | null>(null);

/**
 * 删除一次模拟考（fix-22）。
 *
 * 流程：popconfirm 二次确认 → DELETE /exams/{id} → 重新拉仪表盘。
 *
 * 参数:
 *   attemptId: 要删除的 attempt 主键
 */
async function handleDelete(attemptId: number): Promise<void> {
  deletingId.value = attemptId;
  try {
    await deleteExam(attemptId);
    ElMessage.success('已删除');
    await dashboard.fetch(); // 重新拉数据（含 ECharts 重绘）
    // 重新触发图表渲染（dashboard.data 已变更）
    requestAnimationFrame(() => initCharts());
  } catch (err) {
    const msg = (err as { message?: string })?.message ?? '删除失败';
    ElMessage.error(msg);
  } finally {
    deletingId.value = null;
  }
}

// 测试钩子：暴露 handleDelete 给单元测试（避免 happy-dom 中 el-popconfirm
// DOM 行为不稳定的问题）。生产代码不依赖此暴露。
defineExpose({
  $_handleDeleteForTest: handleDelete,
});
</script>

<template>
  <div class="page-container">
    <h1>📈 学习仪表盘</h1>

    <el-row :gutter="16">
      <el-col :xs="24" :sm="8">
        <div class="section-card stat-mini">
          <div class="stat-mini-label">总考试次数</div>
          <div class="stat-mini-value">{{ dashboard.data?.total_attempts ?? 0 }}</div>
        </div>
      </el-col>
      <el-col :xs="24" :sm="8">
        <div class="section-card stat-mini">
          <div class="stat-mini-label">历次平均分</div>
          <div class="stat-mini-value">{{ avgScore }}</div>
        </div>
      </el-col>
      <el-col :xs="24" :sm="8">
        <div class="section-card stat-mini">
          <div class="stat-mini-label">历次最高分</div>
          <div class="stat-mini-value">{{ maxScore }}</div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :xs="24" :md="16">
        <div class="section-card">
          <div ref="trendEl" class="chart-box chart-box-tall"></div>
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="section-card delta-card" :class="trendDelta >= 0 ? 'up' : 'down'">
          <div class="delta-label">最近一次</div>
          <div class="delta-value">
            <span :class="trendDelta >= 0 ? 'up-arrow' : 'down-arrow'">
              {{ trendDelta >= 0 ? '↑' : '↓' }}
            </span>
            <span class="delta-num">{{ Math.abs(trendDelta) }}</span>
          </div>
          <div class="delta-desc">
            相比上次
            {{ trendDelta >= 0 ? '进步' : '退步' }}
          </div>
        </div>
      </el-col>
    </el-row>

    <div class="section-card">
      <div ref="radarEl" class="chart-box chart-box-tall"></div>
    </div>

    <div v-if="dashboard.data && dashboard.data.history.length > 0" class="section-card">
      <h2>📜 历次成绩明细</h2>
      <el-table :data="dashboard.data.history.slice().reverse()" stripe>
        <el-table-column label="时间" width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.started_at) }}
          </template>
        </el-table-column>
        <el-table-column label="交卷时间" width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.submitted_at) }}
          </template>
        </el-table-column>
        <el-table-column label="得分" width="120">
          <template #default="{ row }">
            <strong>{{ row.total_score }}</strong> / 110
          </template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button size="small" @click="router.push(`/exam/${row.attempt_id}/result`)">
              查看成绩
            </el-button>
            <el-popconfirm
              title="确定删除该次考试记录？删除后不可恢复"
              confirm-button-text="删除"
              cancel-button-text="取消"
              confirm-button-type="danger"
              @confirm="handleDelete(row.attempt_id)"
            >
              <template #reference>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :loading="deletingId === row.attempt_id"
                >
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
/* 仪表盘 — KPI(stat) + 折线 + 雷达 + 列表（保留 fix-18 删除/时区逻辑） */
h1 {
  margin: 0 0 var(--s-4);
  font: var(--fw-semibold) var(--fs-h1) / var(--lh-tight) var(--font-display);
  color: var(--fg);
  letter-spacing: -0.02em;
}

.stat-mini {
  text-align: center;
  padding: var(--s-5) var(--s-4);
}
.stat-mini-label {
  font-size: var(--fs-caption);
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: var(--s-2);
}
.stat-mini-value {
  font: 700 28px / 1 var(--font-display);
  color: var(--fg);
  letter-spacing: -0.01em;
}

.chart-box {
  width: 100%;
  height: 320px;
}
.chart-box-tall {
  height: 360px;
}

/* 趋势对比卡 — 柔色背景 + 大字 */
.delta-card {
  text-align: center;
  padding: var(--s-8) var(--s-5);
  display: flex;
  flex-direction: column;
  justify-content: center;
  height: 360px;
  background: var(--surface-2);
  border: 1px solid var(--border);
}
.delta-card.up   { background: var(--success-soft); border-color: var(--success); }
.delta-card.down { background: var(--danger-soft);  border-color: var(--danger); }
.delta-label {
  font-size: var(--fs-body);
  color: inherit;
  opacity: 0.8;
  margin-bottom: var(--s-3);
}
.delta-value {
  font: 700 48px / 1 var(--font-display);
  margin: var(--s-2) 0;
  letter-spacing: -0.02em;
}
.delta-num { margin-left: 4px; }
.up-arrow   { color: var(--success); }
.down-arrow { color: var(--danger); }
.delta-desc {
  font-size: var(--fs-body);
  color: inherit;
  opacity: 0.8;
  margin-top: var(--s-3);
}

/* 历次成绩明细表 — 卡片化 */
.section-card :deep(.el-table) {
  --el-table-border-color: var(--border-soft);
  --el-table-header-bg-color: var(--surface-2);
}
.section-card :deep(.el-table th.el-table__cell) {
  background: var(--surface-2);
  color: var(--fg-2);
  font-weight: var(--fw-semibold);
}
.section-card :deep(.el-table tr) {
  background: var(--surface) !important;
}
</style>
