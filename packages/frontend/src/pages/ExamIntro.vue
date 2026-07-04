<script setup lang="ts">
/**
 * /exam/:id/intro 考试介绍页。
 *
 * 展示：
 *  - 时长 / 总分 / 题型分布
 *  - "开始答题" 按钮 → /exam/:id/play
 *  - "返回首页" 按钮
 */
import { computed, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { useExamStore } from '@/stores/exam';
import type { QuestionType } from '@/types/api';
import { formatDateTime } from '@/utils/format';

const route = useRoute();
const router = useRouter();
const exam = useExamStore();

const attemptId = computed(() => Number(route.params.id));

onMounted(async () => {
  // 如果 store 中不是当前 attemptId（断线重连场景），重新拉取
  if (exam.attemptId !== attemptId.value) {
    try {
      await exam.loadExisting(attemptId.value);
    } catch (err) {
      const msg = (err as { message?: string })?.message ?? '加载失败';
      ElMessage.error(msg);
      router.push('/');
    }
  }
});

/** 题型 label 映射。 */
const TYPE_LABELS: Record<QuestionType, string> = {
  single: '单选题',
  multi: '多选题',
  judge: '判断题',
  calc: '计算分析题',
  comprehensive: '综合题',
};

/** 统计各题型数量（runtime 中 comprehensive 会 fallback 到 calc）。 */
const typeStats = computed(() => {
  const stats: Record<string, number> = {};
  for (const q of exam.questions) {
    stats[q.type] = (stats[q.type] || 0) + 1;
  }
  // 按固定顺序输出
  return (['single', 'multi', 'judge', 'calc', 'comprehensive'] as QuestionType[])
    .filter((t) => stats[t])
    .map((t) => ({ type: t, count: stats[t] }));
});

/** 实际展示顺序。 */
const orderedTypes = computed(() => typeStats.value);

/** 开始答题。 */
function handleStart(): void {
  router.push(`/exam/${attemptId.value}/play`);
}
</script>

<template>
  <div class="page-container">
    <div class="section-card intro-header">
      <h1>📝 模拟考开始前</h1>
      <p class="intro-sub">请确认以下信息，准备好后点击"开始答题"</p>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :sm="8">
        <div class="section-card stat-mini">
          <div class="stat-mini-label">⏱️ 考试时长</div>
          <div class="stat-mini-value">{{ exam.timeLimitMinutes }} <span>分钟</span></div>
        </div>
      </el-col>
      <el-col :xs="24" :sm="8">
        <div class="section-card stat-mini">
          <div class="stat-mini-label">📊 试卷总分</div>
          <div class="stat-mini-value">{{ exam.totalScore }} <span>分</span></div>
        </div>
      </el-col>
      <el-col :xs="24" :sm="8">
        <div class="section-card stat-mini">
          <div class="stat-mini-label">📋 题目数量</div>
          <div class="stat-mini-value">{{ exam.totalQuestions }} <span>题</span></div>
        </div>
      </el-col>
    </el-row>

    <div class="section-card">
      <h2>题型分布</h2>
      <el-table :data="typeStats" stripe>
        <el-table-column label="题型">
          <template #default="{ row }">
            <el-tag :type="row.type === 'single' ? 'success' : row.type === 'multi' ? 'warning' : row.type === 'judge' ? 'info' : 'danger'">
              {{ TYPE_LABELS[row.type as QuestionType] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="count" label="题目数" width="120" />
        <el-table-column label="每题分值" width="120">
          <template #default="{ row }">
            {{
              row.type === 'single' ? '2 分' :
              row.type === 'multi' ? '3 分' :
              row.type === 'judge' ? '1 分' :
              row.type === 'calc' ? '5 分' :
              '10 分'
            }}
          </template>
        </el-table-column>
        <el-table-column label="小计分值">
          <template #default="{ row }">
            {{ row.count * (row.type === 'single' ? 2 : row.type === 'multi' ? 3 : row.type === 'judge' ? 1 : row.type === 'calc' ? 5 : 10) }} 分
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="section-card notice">
      <h3>📌 考前须知</h3>
      <ul>
        <li>考试过程中请勿刷新页面（草稿会自动每 30 秒保存到本地）</li>
        <li>倒计时归零时系统会自动提交</li>
        <li>未作答的题目将记 0 分（<strong>无猜测惩罚</strong>）</li>
        <li>主观题（计算/综合）按关键词覆盖率给分（≥ 60% 起步）</li>
        <li>交卷前会校验未答题数，弹窗二次确认</li>
      </ul>
    </div>

    <div class="action-bar">
      <el-button size="large" @click="router.push('/')">返回首页</el-button>
      <el-button type="primary" size="large" @click="handleStart">
        🚀 开始答题
      </el-button>
    </div>
  </div>
</template>

<style scoped>
/* 考试介绍页 — intro-header + 3 stat + 题型分布 + notice + action-bar */
.intro-header {
  text-align: center;
  padding: var(--s-10) var(--s-5) var(--s-8);
  background:
    linear-gradient(135deg, var(--sky-soft) 0%, var(--sunrise-soft) 100%);
  border: 1px solid var(--sky-fog);
}
.intro-header h1 {
  margin: 0 0 var(--s-2);
  font: var(--fw-bold) var(--fs-h1) / var(--lh-tight) var(--font-display);
  color: var(--fg);
  letter-spacing: -0.02em;
}
.intro-sub {
  color: var(--muted);
  margin: 0;
  font-size: var(--fs-body-lg);
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
.stat-mini-value span {
  font-size: var(--fs-body);
  color: var(--muted);
  font-weight: 400;
  margin-left: 4px;
}

.notice {
  background: var(--sky-soft);
  border-left: 3px solid var(--sky);
  padding: var(--s-4) var(--s-5);
}
.notice h3 {
  margin: 0 0 var(--s-3);
  color: var(--sky-active);
  font-size: var(--fs-h4);
}
.notice ul {
  margin: 0;
  padding-left: var(--s-5);
  color: var(--fg-2);
  line-height: var(--lh-base);
  font-size: var(--fs-body);
}
.notice ul li { margin-bottom: var(--s-1); }
.notice strong { color: var(--sky-active); font-weight: var(--fw-semibold); }

.action-bar {
  display: flex;
  gap: var(--s-3);
  justify-content: center;
  margin-top: var(--s-5);
  margin-bottom: var(--s-6);
  flex-wrap: wrap;
}

/* Element Plus table 边框融入 */
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
