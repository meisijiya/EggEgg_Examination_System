<script setup lang="ts">
/**
 * / 首页 — 最近成绩 + "开始模拟考" 按钮（带模式选择）。
 *
 * 展示：
 *  - 最近 1 次成绩（大卡片）
 *  - "开始模拟考" 按钮 → 弹模式选择 modal → POST /exams/start → 跳 /exam/:id/intro
 *  - 历次成绩迷你列表
 *  - 总平均分
 *
 * fix-22 改造：
 * - 新增 el-dialog 模式选择（标准 / 混合）
 * - 启动考试时透传 mode 给后端
 * - 时间展示统一走 formatDateTime（强制 Shanghai 时区）
 */
import { onMounted, ref, defineExpose, computed } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { useDashboardStore } from '@/stores/dashboard';
import { useExamStore } from '@/stores/exam';
import { formatDateTime } from '@/utils/format';

const router = useRouter();
const dashboard = useDashboardStore();
const exam = useExamStore();

const starting = ref<boolean>(false);
/** 模式选择 modal 可见性。 */
const modeDialogVisible = ref<boolean>(false);
/** 当前选中的模式（el-radio-group v-model）。 */
const selectedMode = ref<'standard' | 'mixed'>('standard');

/**
 * fix-30a:当前选中的 subject — 直接读 exam store,与顶栏 SubjectSwitcher 同步。
 * 若从未选过,fallback 用 id='fin-mgmt'(单科目兜底)。
 */
const currentSubjectName = computed(() => {
  return exam.currentSubject?.name ?? '财务管理';
});

onMounted(async () => {
  await dashboard.fetch();
});

/** 最近一次成绩。 */
const latest = () => {
  const h = dashboard.data?.history;
  if (!h || h.length === 0) return null;
  return h[h.length - 1];
};

/** 历次平均分。 */
const avgScore = () => {
  const h = dashboard.data?.history;
  if (!h || h.length === 0) return 0;
  const sum = h.reduce((s, a) => s + a.total_score, 0);
  return Math.round((sum / h.length) * 10) / 10;
};

/** 打开模式选择 modal（不立即启动）。 */
function handleStart(): void {
  modeDialogVisible.value = true;
}

/**
 * 启动超过 ~30s 时给用户友好提示 — mixed 模式要串行调 ~13 次 LLM,
 * 实测 30-200s 之间,如果不告诉用户进度,容易被认为是"卡死 / 失败"。
 */
let slowTipTimer: ReturnType<typeof setTimeout> | null = null;
function showSlowTipOnce(): void {
  if (slowTipTimer !== null) return;
  slowTipTimer = setTimeout(() => {
    ElMessage.info('正在准备混合模式试卷，约需 1-3 分钟，请耐心等待…');
    slowTipTimer = null;
  }, 30_000);
}
function clearSlowTip(): void {
  if (slowTipTimer !== null) {
    clearTimeout(slowTipTimer);
    slowTipTimer = null;
  }
}

/**
 * 确认模式后启动一次新考试。
 *
 * 参数:
 *   mode: 'standard' / 'mixed' — 来自 el-radio-group v-model
 *
 * 异常分流:
 * - exam.startNew 内的 token 防御会抛"登录已过期, 请重新登录"
 * - axios 401 拦截器会清 token + 跳 /login?reason=expired(页面刷)
 *   但 mixed 模式若中途拿到 401 + 当前 catch 已触发(axios 拦截器在
 *   catch 后才执行), 我们这里也判断一下消息含"登录/auth/token"
 *   时手动跳一次以避免卡死。
 */
async function confirmStart(
  mode: 'standard' | 'mixed',
): Promise<void> {
  starting.value = true;
  showSlowTipOnce();
  try {
    const resp = await exam.startNew(mode);
    ElMessage.success(`试卷已生成（${resp.questions.length} 题）`);
    modeDialogVisible.value = false;
    router.push(`/exam/${resp.attempt_id}/intro`);
  } catch (err) {
    const msg = (err as { message?: string })?.message ?? '启动失败';
    if (/登录|auth|token|401/i.test(msg)) {
      ElMessage.error('登录已过期，请重新登录');
      router.push('/login?reason=expired');
    } else {
      ElMessage.error(msg);
    }
  } finally {
    clearSlowTip();
    starting.value = false;
  }
}

// 测试钩子：暴露 selectedMode 和 modeDialogVisible 给单元测试
// （避免 happy-dom 中 el-radio-group v-model 同步不稳定的问题）。
// 生产代码不依赖此暴露。
defineExpose({
  selectedMode,
  modeDialogVisible,
});
</script>

<template>
  <div class="page-container">
    <div class="hero">
      <h1>欢迎回来 👋</h1>
      <p class="hero-sub">坚持每天 1 套模拟考，财务管理不再难</p>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :sm="12">
        <div class="section-card stat-card">
          <div class="stat-label">最近一次成绩</div>
          <div v-if="latest()" class="stat-value">
            {{ latest()!.total_score }}
            <span class="stat-unit"> / {{ exam.totalScore || 110 }} 分</span>
          </div>
          <div v-else class="stat-empty">暂无考试记录</div>
          <div v-if="latest()" class="stat-time">
            {{ formatDateTime(latest()!.started_at) }}
          </div>
        </div>
      </el-col>
      <el-col :xs="24" :sm="12">
        <div class="section-card stat-card">
          <div class="stat-label">历史平均分</div>
          <div class="stat-value">{{ avgScore() }} <span class="stat-unit">分</span></div>
          <div class="stat-time">共 {{ dashboard.data?.total_attempts ?? 0 }} 次考试</div>
        </div>
      </el-col>
    </el-row>

    <div class="section-card start-card">
      <div class="subject-tag">
        <el-tag size="large" type="info" effect="dark">
          📚 当前科目: {{ currentSubjectName }}
        </el-tag>
      </div>
      <h2>开始一次模拟考</h2>
      <p class="start-desc">
        系统将按章节×题型×难度三维加权，从该科目题库中抽取 41 道题（共 110 分，限时 120 分钟）。
      </p>
      <el-button
        type="primary"
        size="large"
        :loading="starting"
        @click="handleStart"
      >
        🚀 开始模拟考
      </el-button>
    </div>

    <div v-if="dashboard.data && dashboard.data.history.length > 0" class="section-card">
      <h2>最近 5 次成绩</h2>
      <div class="table-responsive">
        <el-table :data="dashboard.data.history.slice(-5).reverse()" stripe>
        <el-table-column label="时间" width="220">
          <template #default="{ row }">
            {{ formatDateTime(row.started_at) }}
          </template>
        </el-table-column>
        <el-table-column label="得分" width="180">
          <template #default="{ row }">
            <strong>{{ row.total_score }}</strong> / {{ exam.totalScore || 110 }}
          </template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button size="small" @click="router.push(`/exam/${row.attempt_id}/result`)">
              查看成绩
            </el-button>
          </template>
        </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 模式选择 modal（fix-22） -->
    <el-dialog
      v-model="modeDialogVisible"
      title="选择出题模式"
      width="480px"
      :close-on-click-modal="false"
    >
      <p class="modal-tip">不同模式的题目构成不同，请根据需求选择：</p>
      <el-radio-group v-model="selectedMode" class="mode-radio-group">
        <el-radio-button value="standard" size="large">
          <div class="mode-option">
            <div class="mode-title">📚 标准模式</div>
            <div class="mode-desc">原题库 41 道，覆盖 9 章 × 5 题型</div>
            <div class="mode-tag">推荐</div>
          </div>
        </el-radio-button>
        <el-radio-button value="mixed" size="large">
          <div class="mode-option">
            <div class="mode-title">🎲 混合模式</div>
            <div class="mode-desc">部分原题 + 部分改编题，更接近真实考试</div>
            <div class="mode-tag muted">Beta（当前行为等同标准）</div>
          </div>
        </el-radio-button>
      </el-radio-group>
      <template #footer>
        <el-button @click="modeDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="starting" @click="confirmStart(selectedMode)">
          开始答题
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 首页 — hero + pill-row + KPI(stat) + mode modal + btn-sun */
.hero {
  text-align: center;
  margin: var(--s-8) 0 var(--s-10);
}
.hero h1 {
  font: var(--fw-bold) clamp(32px, 5vw, var(--fs-display)) / 1.2 var(--font-display);
  letter-spacing: -0.02em;
  margin: 0 0 var(--s-3);
  color: var(--fg);
}
.hero-sub {
  color: var(--muted);
  font-size: var(--fs-body-lg);
  margin: 0 0 var(--s-5);
}

.stat-card {
  text-align: center;
  padding: var(--s-6) var(--s-5);
}
.stat-label {
  font-size: var(--fs-caption);
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: var(--s-2);
}
.stat-value {
  font: 700 32px / 1 var(--font-display);
  color: var(--fg);
  margin: var(--s-1) 0;
  letter-spacing: -0.01em;
}
.stat-unit {
  font-size: var(--fs-body-lg);
  color: var(--muted);
  font-weight: 400;
}
.stat-empty {
  font-size: var(--fs-body-lg);
  color: var(--muted-2);
  padding: var(--s-3) 0;
}
.stat-time {
  font-size: var(--fs-caption);
  color: var(--muted-2);
  margin-top: var(--s-2);
  font-family: var(--font-mono);
}

.start-card {
  text-align: center;
  padding: var(--s-8) var(--s-5);
  background:
    linear-gradient(135deg, var(--sky-soft), var(--sunrise-soft));
  border: 1px solid var(--sky-fog);
}
.subject-tag {
  display: flex;
  justify-content: center;
  margin-bottom: var(--s-3);
}
.subject-tag :deep(.el-tag) {
  font-size: var(--fs-body-lg);
  padding: var(--s-2) var(--s-4);
  border-radius: var(--r-pill);
  background: var(--surface) !important;
  color: var(--sky-active) !important;
  border: 1.5px solid var(--sky) !important;
}
.start-card h2 {
  margin: 0 0 var(--s-2);
  font: var(--fw-semibold) var(--fs-h2) / var(--lh-tight) var(--font-display);
  color: var(--fg);
}
.start-desc {
  color: var(--fg-2);
  margin: 0 auto var(--s-5);
  font-size: var(--fs-body);
  line-height: var(--lh-snug);
  max-width: 560px;
}

/* 模式选择 modal */
.modal-tip {
  color: var(--muted);
  font-size: var(--fs-body);
  margin: 0 0 var(--s-4);
}
.mode-radio-group {
  display: flex;
  flex-direction: column;
  gap: var(--s-3);
  width: 100%;
}
.mode-radio-group :deep(.el-radio-button) {
  margin-right: 0;
  width: 100%;
}
.mode-radio-group :deep(.el-radio-button__inner) {
  width: 100%;
  padding: var(--s-4) var(--s-5);
  border-radius: var(--r-md) !important;
  text-align: left;
  border: 1.5px solid var(--border) !important;
  background: var(--surface) !important;
  color: var(--fg) !important;
}
.mode-radio-group :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--sky-soft) !important;
  border-color: var(--sky) !important;
  color: var(--fg) !important;
  box-shadow: var(--shadow-sky) !important;
}
.mode-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mode-title {
  font-size: var(--fs-body-lg);
  font-weight: var(--fw-semibold);
  color: var(--fg);
}
.mode-desc {
  font-size: var(--fs-body);
  color: var(--muted);
}
.mode-tag {
  display: inline-block;
  margin-top: 4px;
  padding: 2px var(--s-2);
  background: var(--success-soft);
  color: var(--success);
  border-radius: var(--r-sm);
  font-size: var(--fs-caption);
  width: fit-content;
  font-weight: var(--fw-medium);
}
.mode-tag.muted {
  background: var(--surface-2);
  color: var(--muted);
}

/* Element Plus table 卡片化（最近 5 次成绩） */
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