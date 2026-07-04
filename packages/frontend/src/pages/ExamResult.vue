<script setup lang="ts">
/**
 * /exam/:id/result 成绩页。
 *
 * 展示：
 *  - 总分（满分 110）
 *  - 章节分雷达图（ECharts）
 *  - 类型分柱状图
 *  - 每题评语折叠面板（答对默认折叠，答错默认展开）
 *  - "AI 讲解"按钮（调 ExplainPanel）
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import * as echarts from 'echarts/core';
import { RadarChart, BarChart } from 'echarts/charts';
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  PolarComponent,
  GridComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { getResult } from '@/api';
import type { ExamResult } from '@/types/api';
import QuestionCard from '@/components/QuestionCard.vue';
import ExplainPanel from '@/components/ExplainPanel.vue';
import { useExamStore } from '@/stores/exam';
import { formatDateTime } from '@/utils/format';

echarts.use([
  RadarChart,
  BarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  PolarComponent,
  GridComponent,
  CanvasRenderer,
]);

const route = useRoute();
const router = useRouter();
const exam = useExamStore();

const attemptId = computed(() => Number(route.params.id));
const result = ref<ExamResult | null>(null);
const loading = ref<boolean>(false);

const radarEl = ref<HTMLDivElement | null>(null);
const barEl = ref<HTMLDivElement | null>(null);
let radarChart: echarts.ECharts | null = null;
let barChart: echarts.ECharts | null = null;

const explainVisible = ref<boolean>(false);
const explainQuestionId = ref<number>(0);
/** 折叠面板展开项 — 错题 / 半对题默认展开。 */
const activeNames = ref<number[]>([]);

const TYPE_LABELS: Record<string, string> = {
  single: '单选',
  multi: '多选',
  judge: '判断',
  calc: '计算',
  comprehensive: '综合',
};

onMounted(async () => {
  loading.value = true;
  try {
    const r = await getResult(attemptId.value);
    result.value = r;
    // 默认展开错题 / 半对题
    activeNames.value = r.answers
      .filter((a) => a.awarded_score < a.full_score)
      .map((a) => a.question_id);
    // 等下个 tick DOM 完成
    requestAnimationFrame(() => {
      initCharts();
    });
  } catch (e) {
    const msg = (e as { message?: string })?.message ?? '加载成绩失败';
    ElMessage.error(msg);
  } finally {
    loading.value = false;
  }
});

/** 章节分雷达图数据。 */
const chapterRadarData = computed(() => {
  if (!result.value) return { indicators: [], values: [] };
  const codes = Object.keys(result.value.score_by_chapter).sort();
  const indicators = codes.map((c) => ({ name: c, max: 25 })); // 粗略 max
  const values = codes.map(
    (c) => result.value!.score_by_chapter[c] ?? 0,
  );
  return { indicators, values };
});

/** 类型分柱状图数据。 */
const typeBarData = computed(() => {
  if (!result.value) return { names: [], awarded: [], full: [] };
  const types = Object.keys(result.value.score_by_type);
  const names = types.map((t) => TYPE_LABELS[t] || t);
  const awarded = types.map((t) => result.value!.score_by_type[t] ?? 0);
  // 从 answers 推算各类型满分（用 full_score 求和）
  const full = types.map((t) => {
    return result.value!.answers
      .filter((a) => a.type === t)
      .reduce((s, a) => s + a.full_score, 0);
  });
  return { names, awarded, full };
});

function initCharts(): void {
  if (radarEl.value) {
    radarChart = echarts.init(radarEl.value);
    radarChart.setOption({
      title: { text: '章节得分', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: {},
      radar: {
        indicator: chapterRadarData.value.indicators.map((d) => ({
          name: d.name,
          max: Math.max(...chapterRadarData.value.values, 25) + 5,
        })),
        radius: '65%',
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: chapterRadarData.value.values,
              name: '得分',
              areaStyle: { color: 'rgba(91, 160, 226, 0.25)' },
              lineStyle: { color: '#5BA0E2' },
              itemStyle: { color: '#5BA0E2' },
            },
          ],
        },
      ],
    });
  }
  if (barEl.value) {
    barChart = echarts.init(barEl.value);
    barChart.setOption({
      title: { text: '题型得分', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      legend: { data: ['得分', '满分'], bottom: 0 },
      grid: { left: 40, right: 20, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: typeBarData.value.names },
      yAxis: { type: 'value' },
      series: [
        {
          name: '得分',
          type: 'bar',
          data: typeBarData.value.awarded,
          itemStyle: { color: '#F4A574' },  /* sunrise — chart-2 */
        },
        {
          name: '满分',
          type: 'bar',
          data: typeBarData.value.full,
          itemStyle: { color: '#E5EAF0' },  /* muted slate */
        },
      ],
    });
  }
}

/** 答对 / 答错 / 半对 判定。 */
function isAnsweredCorrect(
  isCorrect: boolean | null,
  awarded: number,
  full: number,
): 'correct' | 'wrong' | 'partial' {
  if (isCorrect === true || (isCorrect === null && awarded >= full)) return 'correct';
  if (awarded === 0) return 'wrong';
  return 'partial';
}

/** 答对 / 答错题数。 */
const correctCount = computed(() => {
  if (!result.value) return 0;
  return result.value.answers.filter(
    (a) => a.is_correct === true || a.awarded_score >= a.full_score,
  ).length;
});

const wrongCount = computed(() => {
  if (!result.value) return 0;
  return result.value.answers.filter((a) => a.awarded_score === 0).length;
});

const partialCount = computed(() => {
  if (!result.value) return 0;
  return result.value.answers.filter(
    (a) => a.awarded_score > 0 && a.awarded_score < a.full_score,
  ).length;
});

/** 打开 AI 讲解。 */
function openExplain(questionId: number): void {
  explainQuestionId.value = questionId;
  explainVisible.value = true;
}

/** 重新考试。 */
function restartExam(): void {
  exam.reset();
  router.push('/');
}
</script>

<template>
  <div class="page-container">
    <div v-if="loading" class="loading">
      <el-icon class="is-loading"><i class="el-icon-loading" /></el-icon>
      加载成绩中...
    </div>

    <div v-else-if="result">
      <!-- 总分大卡片 -->
      <div class="section-card result-hero">
        <div class="result-score">
          <div class="score-label">本次考试总分</div>
          <div class="score-value">{{ result.total_score }}<span class="score-max"> / 110</span></div>
          <div class="score-time">{{ formatDateTime(result.submitted_at) }}</div>
        </div>
        <el-row :gutter="12" class="result-stats">
          <el-col :span="8">
            <div class="stat-mini stat-correct">
              <div class="stat-num">{{ correctCount }}</div>
              <div class="stat-lbl">答对</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-mini stat-partial">
              <div class="stat-num">{{ partialCount }}</div>
              <div class="stat-lbl">部分得分</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-mini stat-wrong">
              <div class="stat-num">{{ wrongCount }}</div>
              <div class="stat-lbl">答错/未答</div>
            </div>
          </el-col>
        </el-row>
      </div>

      <!-- 图表 -->
      <el-row :gutter="16" class="charts-row">
        <el-col :xs="24" :md="12">
          <div class="section-card">
            <div ref="radarEl" class="chart-box"></div>
          </div>
        </el-col>
        <el-col :xs="24" :md="12">
          <div class="section-card">
            <div ref="barEl" class="chart-box"></div>
          </div>
        </el-col>
      </el-row>

      <!-- 每题评语 -->
      <div class="section-card">
        <h2>📝 题目评语</h2>
        <p class="sub-tip">点击题目展开 / 折叠详情，错题默认展开</p>
        <el-collapse v-model="activeNames">
          <el-collapse-item
            v-for="(a, idx) in result.answers"
            :key="a.question_id"
            :name="a.question_id"
          >
            <template #title>
              <div class="qa-title">
                <el-tag
                  size="small"
                  :type="isAnsweredCorrect(a.is_correct, a.awarded_score, a.full_score) === 'correct' ? 'success' : isAnsweredCorrect(a.is_correct, a.awarded_score, a.full_score) === 'wrong' ? 'danger' : 'warning'"
                >
                  {{ TYPE_LABELS[a.type] || a.type }}
                </el-tag>
                <span class="qa-seq">第 {{ a.sequence }} 题</span>
                <span class="qa-stem-text">{{ a.stem.slice(0, 40) }}{{ a.stem.length > 40 ? '...' : '' }}</span>
                <span class="qa-score">{{ a.awarded_score }} / {{ a.full_score }} 分</span>
                <span v-if="a.sub_answer_count && a.sub_answer_count >= 2" class="qa-meta">
                  识别到 {{ a.sub_answer_count }} 个分小问作答
                </span>
                <span v-if="isAnsweredCorrect(a.is_correct, a.awarded_score, a.full_score) === 'correct'" class="qa-mark correct">✓</span>
                <span v-else-if="isAnsweredCorrect(a.is_correct, a.awarded_score, a.full_score) === 'wrong'" class="qa-mark wrong">✗</span>
                <span v-else class="qa-mark partial">◐</span>
              </div>
            </template>
            <div class="qa-detail">
              <QuestionCard
                :question="{
                  id: a.question_id,
                  type: a.type,
                  chapter_id: 0,
                  chapter_code: a.chapter_code,
                  difficulty: 1,
                  stem: a.stem,
                  options: null,
                  score: a.full_score,
                  sequence: a.sequence,
                }"
                :user-answer="a.user_answer"
                :readonly="true"
                :show-correct="true"
                :correct-answer="a.correct_answer"
                :comment="a.comment"
              />
              <div class="qa-actions">
                <el-button type="primary" plain @click="openExplain(a.question_id)">
                  🤖 AI 讲解
                </el-button>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>

      <div class="action-bar">
        <el-button size="large" @click="router.push('/')">返回首页</el-button>
        <el-button size="large" @click="router.push('/dashboard')">查看仪表盘</el-button>
        <el-button type="primary" size="large" @click="restartExam">再考一次</el-button>
      </div>

      <ExplainPanel
        v-model:visible="explainVisible"
        :attempt-id="attemptId"
        :question-id="explainQuestionId"
      />
    </div>
  </div>
</template>

<style scoped>
/* 成绩页 — hero stat + 图表 + 评语折叠 + 操作栏 */
.loading {
  text-align: center;
  padding: var(--s-16) var(--s-5);
  color: var(--muted);
  font-size: var(--fs-body-lg);
}

/* 总分 hero — 蓝天 / 旭日 渐变背景 */
.result-hero {
  text-align: center;
  padding: var(--s-10) var(--s-5);
  background:
    linear-gradient(135deg, var(--sky-soft) 0%, var(--sunrise-soft) 100%);
  border: 1px solid var(--sky-fog);
  position: relative;
  overflow: hidden;
}
.score-label {
  font: 500 var(--fs-caption) / 1 var(--font-body);
  color: var(--sky-active);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: var(--s-3);
}
.score-value {
  font: 700 56px / 1 var(--font-display);
  color: var(--fg);
  margin: var(--s-2) 0;
  letter-spacing: -0.02em;
}
.score-max {
  font-size: var(--fs-h3);
  color: var(--muted);
  font-weight: 400;
}
.score-time {
  font-size: var(--fs-caption);
  color: var(--muted-2);
  margin-top: var(--s-2);
  font-family: var(--font-mono);
}

.result-stats { margin-top: var(--s-6); }
.stat-mini {
  text-align: center;
  padding: var(--s-4) var(--s-3);
  border-radius: var(--r-md);
  background: var(--surface);
}
.stat-correct { background: var(--success-soft); color: var(--success); }
.stat-partial { background: var(--warning-soft); color: oklch(50% 0.12 82); }
.stat-wrong   { background: var(--danger-soft); color: var(--danger); }
.stat-num {
  font: 700 24px / 1 var(--font-display);
}
.stat-lbl {
  font-size: var(--fs-caption);
  margin-top: 4px;
  opacity: 0.85;
}

.charts-row { margin-bottom: 0; }
.chart-box {
  width: 100%;
  height: 300px;
}

.sub-tip {
  font-size: var(--fs-body);
  color: var(--muted);
  margin: var(--s-1) 0 var(--s-4);
}

/* 评语折叠 */
.qa-title {
  display: flex;
  align-items: center;
  gap: var(--s-3);
  flex-wrap: wrap;
  flex: 1;
  width: 100%;
}
.qa-title :deep(.el-tag) {
  border-radius: var(--r-sm);
}
.qa-seq {
  color: var(--muted);
  font-size: var(--fs-caption);
  font-family: var(--font-mono);
}
.qa-stem-text {
  color: var(--fg);
  font-size: var(--fs-body);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qa-score {
  font-weight: var(--fw-semibold);
  color: var(--sky-active);
  font-family: var(--font-mono);
  background: var(--sky-soft);
  padding: 2px var(--s-2);
  border-radius: var(--r-sm);
}
.qa-meta {
  font-size: var(--fs-caption);
  color: var(--sky-active);
  background: var(--sky-soft);
  padding: 2px var(--s-2);
  border-radius: var(--r-pill);
}
.qa-mark {
  font-size: 18px;
  font-weight: 700;
}
.qa-mark.correct { color: var(--success); }
.qa-mark.wrong   { color: var(--danger); }
.qa-mark.partial { color: oklch(50% 0.12 82); }

.qa-detail { padding: var(--s-2) 0; }
.qa-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--s-3);
  gap: var(--s-2);
}

.action-bar {
  display: flex;
  gap: var(--s-3);
  justify-content: center;
  margin: var(--s-6) 0;
  flex-wrap: wrap;
}

/* 折叠面板：圆角、focus 态柔和 */
.qa-list :deep(.el-collapse-item__header) {
  padding: var(--s-3) var(--s-4);
  border-bottom: 1px solid var(--border-soft);
  background: var(--surface);
}
.qa-list :deep(.el-collapse-item__content) {
  padding: var(--s-3) var(--s-4);
  background: var(--surface-2);
}

/* table 边框融入 */
.section-card :deep(.el-table) {
  --el-table-border-color: var(--border-soft);
  --el-table-header-bg-color: var(--surface-2);
}
.section-card :deep(.el-table th.el-table__cell) {
  background: var(--surface-2);
  color: var(--fg-2);
  font-weight: var(--fw-semibold);
}
</style>
