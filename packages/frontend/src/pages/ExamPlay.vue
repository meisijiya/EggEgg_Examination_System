<script setup lang="ts">
/**
 * /exam/:id/play 答题页 — 分题型分卡片，顶部倒计时，提交前校验。
 *
 * 关键交互：
 *  - 顶部倒计时：基于 store.deadlineMs() 实时计算（1s tick）
 *  - 题目分 5 Section 卡片（按题型），可点击切题
 *  - 答题时 store.setAnswer → 30s 兜底写 localStorage
 *  - 切题前自动保存草稿
 *  - 倒计时归零 → 自动提交
 *  - 提交按钮：未答题数 > 0 → 二次确认弹窗
 *  - 倒计时 ≤ 5 分钟：变红色
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useExamStore } from '@/stores/exam';
import QuestionCard from '@/components/QuestionCard.vue';
import { formatChapterCode } from '@/utils/formatChapterCode';
import type { QuestionType } from '@/types/api';

const route = useRoute();
const router = useRouter();
const exam = useExamStore();

const attemptId = computed(() => Number(route.params.id));
const submitting = ref<boolean>(false);

// ---------- 计时器 ----------
let timer: ReturnType<typeof setInterval> | null = null;
let saveTimer: ReturnType<typeof setInterval> | null = null;

const now = ref<number>(Date.now());

/** 剩余秒数。 */
const remainingSec = computed(() => {
  const ms = exam.deadlineMs() - now.value;
  return Math.max(0, Math.floor(ms / 1000));
});

/**
 * fix-30a:当前科目名 — 显示在顶部信息卡。fallback 到"财务管理"以兼容
 * 历史考试快照(快照可能不携带 subject_id)。
 */
const currentSubjectName = computed(() => {
  return exam.currentSubject?.name ?? '财务管理';
});

/** 格式化倒计时 HH:MM:SS。 */
const remainingText = computed(() => {
  const sec = remainingSec.value;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
});

/** 计时器显示状态颜色。 */
const timerClass = computed(() => {
  if (remainingSec.value <= 300) return 'danger'; // 5min
  if (remainingSec.value <= 900) return 'warn'; // 15min
  return 'safe';
});

// ---------- 题型分组 ----------
const SECTION_TYPES: Array<{ type: QuestionType; label: string }> = [
  { type: 'single', label: '一、单选题' },
  { type: 'multi', label: '二、多选题' },
  { type: 'judge', label: '三、判断题' },
  { type: 'calc', label: '四、计算分析题' },
  { type: 'comprehensive', label: '五、综合题' },
];

const sections = computed(() => {
  return SECTION_TYPES.map((s) => {
    const qs = (exam.questionsByType[s.type] || []).slice().sort(
      (a, b) => a.sequence - b.sequence,
    );
    return { ...s, questions: qs };
  }).filter((s) => s.questions.length > 0);
});

/** 当前显示的题目 sequence 集合（按题型分组）。 */
const currentQuestion = computed(() => {
  return exam.currentQuestion;
});

/** 题型分布摘要（顶部展示）。 */
const sectionSummary = computed(() => {
  return sections.value.map((s) => ({
    type: s.type,
    label: s.label.replace(/^[一二三四五六七八九十]+、/, ''),
    total: s.questions.length,
    answered: s.questions.filter((q) => (exam.answers[q.id] || '').length > 0).length,
  }));
});

/** 未答题数。 */
const unansweredCount = computed(() => {
  return exam.totalQuestions - exam.answeredCount;
});

/** 学员答案写入回调。 */
function onAnswerChange(questionId: number, value: string): void {
  exam.setAnswer(questionId, value);
}

/** 切题。 */
function goToQuestion(sequence: number): void {
  exam.goToQuestion(sequence);
}

/** 提交考试。 */
async function handleSubmit(autoSubmit = false): Promise<void> {
  if (submitting.value) return;
  if (!autoSubmit && unansweredCount.value > 0) {
    try {
      await ElMessageBox.confirm(
        `还有 ${unansweredCount.value} 道题未作答，将记 0 分。是否确认提交？`,
        '未答题提醒',
        {
          confirmButtonText: '确认提交',
          cancelButtonText: '再检查一下',
          type: 'warning',
        },
      );
    } catch {
      return; // 用户取消
    }
  }
  submitting.value = true;
  try {
    exam.saveDraft();
    const resp = await exam.submit();
    ElMessage.success(`交卷成功！得分 ${resp.total_score}`);
    router.push(`/exam/${attemptId.value}/result`);
  } catch (e) {
    const msg = (e as { message?: string })?.message ?? '提交失败';
    ElMessage.error(msg);
  } finally {
    submitting.value = false;
  }
}

// ---------- lifecycle ----------

onMounted(async () => {
  if (exam.attemptId !== attemptId.value || exam.questions.length === 0) {
    try {
      await exam.loadExisting(attemptId.value);
    } catch (err) {
      const msg = (err as { message?: string })?.message ?? '加载失败';
      ElMessage.error(msg);
      router.push('/');
      return;
    }
  }
  // 启动 1s tick
  now.value = Date.now();
  timer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
  // 启动 30s 草稿保存
  saveTimer = setInterval(() => {
    exam.saveDraft();
  }, 30_000);
});

onBeforeUnmount(() => {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (saveTimer) {
    clearInterval(saveTimer);
    saveTimer = null;
  }
  // 离开前保存草稿
  exam.saveDraft();
});

/** 监听倒计时归零。 */
watch(remainingSec, (sec) => {
  if (sec === 0 && !submitting.value) {
    ElMessage.warning('时间到，自动交卷');
    handleSubmit(true);
  }
});

/**
 * exp-1：当前题目的章节指示器（顶部小条）。
 * - 跨章节切换时用 prompt 提示学员（仅在切换瞬间，不是常态干扰）
 * - 同章节内不打扰
 */
const currentChapter = computed(() => {
  const q = exam.questions.find((x) => x.sequence === exam.currentSequence);
  return q?.chapter_code || '';
});

/** 上一次的章节（用于检测切换）。 */
let prevChapter = '';
watch(currentChapter, (cur) => {
  if (prevChapter && cur && prevChapter !== cur) {
    // 跨章节切换时温和提示(不阻塞)— 用 formatChapterCode 转成中文标签
    ElMessage.info(`已切到 ${formatChapterCode(cur)}`);
  }
  prevChapter = cur;
});
</script>

<template>
  <div class="play-page">
    <!-- 顶部固定栏 -->
    <div class="play-header">
      <div class="header-left">
        <span class="header-subject" data-testid="current-subject">
          📚 {{ currentSubjectName }} · 共 {{ exam.totalQuestions }} 题
        </span>
        <span class="header-progress">
          <strong>{{ exam.answeredCount }}</strong> / {{ exam.totalQuestions }} 已答
        </span>
        <span class="header-unanswered" v-if="unansweredCount > 0">
          （未答 {{ unansweredCount }}）
        </span>
        <!-- exp-1：当前题目章节指示器（蓝色 plain tag，与 QuestionCard 章节 tag 一致） -->
        <el-tag
          v-if="currentChapter"
          size="small"
          type="info"
          effect="plain"
          class="header-chapter"
          data-testid="current-chapter-tag"
        >
          {{ formatChapterCode(currentChapter) }}
        </el-tag>
      </div>
      <div :class="['timer-display', timerClass]">⏱ {{ remainingText }}</div>
      <div class="header-right">
        <el-button
          type="primary"
          :loading="submitting"
          @click="handleSubmit(false)"
        >
          📝 交卷
        </el-button>
      </div>
    </div>

    <div class="play-body">
      <!-- 左侧：题目卡片 -->
      <div class="play-main">
        <div v-if="currentQuestion">
          <QuestionCard
            :question="currentQuestion"
            :user-answer="exam.answers[currentQuestion.id] || ''"
            @update:answer="onAnswerChange"
          />
          <div class="nav-buttons">
            <el-button
              :disabled="currentQuestion.sequence <= 1"
              @click="goToQuestion(currentQuestion.sequence - 1)"
            >
              ← 上一题
            </el-button>
            <el-button
              type="primary"
              :disabled="currentQuestion.sequence >= exam.totalQuestions"
              @click="goToQuestion(currentQuestion.sequence + 1)"
            >
              下一题 →
            </el-button>
          </div>
        </div>
        <div v-else class="empty-tip">正在加载题目...</div>
      </div>

      <!-- 右侧：题型分组导航 -->
      <aside class="play-aside">
        <h3>📋 答题卡</h3>
        <div
          v-for="sec in sectionSummary"
          :key="sec.type"
          class="aside-section"
        >
          <div class="aside-section-title">
            {{ sec.label }}
            <span class="aside-progress">{{ sec.answered }}/{{ sec.total }}</span>
          </div>
          <div class="aside-grid">
            <button
              v-for="q in (exam.questionsByType[sec.type] || []).sort((a, b) => a.sequence - b.sequence)"
              :key="q.id"
              :class="[
                'aside-btn',
                exam.currentSequence === q.sequence ? 'current' : '',
                (exam.answers[q.id] || '').length > 0 ? 'answered' : '',
              ]"
              @click="goToQuestion(q.sequence)"
            >
              {{ q.sequence }}
            </button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
/* 答题页 — 顶部固定栏 + 主区 qcard + 右侧答题卡 */
.play-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(1200px 600px at 50% -10%, oklch(92% 0.08 232 / 0.35), transparent 65%),
    var(--bg);
}

/* ---- 顶部固定栏 ---- */
.play-header {
  background: oklch(100% 0 0 / 0.92);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  padding: var(--s-3) var(--s-5);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--s-3);
  box-shadow: var(--shadow-sm);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 50;
}
.header-left {
  font-size: var(--fs-body-lg);
  color: var(--fg);
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--s-3);
  flex-wrap: wrap;
}
.header-subject {
  font-size: var(--fs-body);
  color: var(--sky-active);
  font-weight: var(--fw-medium);
  background: var(--sky-soft);
  padding: 2px var(--s-3);
  border-radius: var(--r-pill);
  white-space: nowrap;
}
.header-progress strong {
  color: var(--sky-active);
  font: 700 22px / 1 var(--font-display);
  font-variant-numeric: tabular-nums;
}
/* exp-1：当前章节 tag 样式（与 .chapter-tag 风格统一） */
.header-chapter {
  letter-spacing: 0.02em;
  flex-shrink: 0;
}
.header-unanswered {
  color: var(--warning);
  font-size: var(--fs-caption);
  background: var(--warning-soft);
  padding: 2px var(--s-2);
  border-radius: var(--r-sm);
}

/* 计时器 — 用 .timer 全局类 + 颜色态 */
.timer-display {
  font-size: var(--fs-h2);
  font-weight: var(--fw-bold);
  font-variant-numeric: tabular-nums;
  display: inline-flex;
  align-items: center;
  gap: var(--s-2);
  padding: 6px var(--s-3);
  border-radius: var(--r-pill);
  background: var(--sunrise-soft);
  color: var(--sunrise-strong);
}
.timer-display.safe { background: var(--success-soft); color: var(--success); }
.timer-display.warn { background: var(--warning-soft); color: oklch(50% 0.12 82); }
.timer-display.danger {
  background: var(--danger-soft);
  color: var(--danger);
  animation: timer-pulse 1.4s infinite;
}
@keyframes timer-pulse {
  50% { transform: scale(1.04); opacity: 0.85; }
}

/* ---- 主体双栏 ---- */
.play-body {
  flex: 1;
  display: flex;
  gap: var(--s-4);
  padding: var(--s-5);
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
}
.play-main {
  flex: 1;
  min-width: 0;
}
.play-aside {
  width: 280px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--s-4);
  height: fit-content;
  position: sticky;
  top: 88px;
  box-shadow: var(--shadow-xs);
}
.play-aside h3 {
  margin: 0 0 var(--s-3);
  font: var(--fw-semibold) var(--fs-h4) / var(--lh-tight) var(--font-display);
  color: var(--fg);
}
.aside-section {
  margin-bottom: var(--s-4);
}
.aside-section-title {
  font: 500 var(--fs-caption) / 1 var(--font-mono);
  color: var(--fg-2);
  margin-bottom: var(--s-2);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--s-1) 0;
  border-bottom: 1px dashed var(--border-soft);
}
.aside-progress {
  color: var(--muted);
  font-size: var(--fs-caption);
  font-family: var(--font-mono);
}
.aside-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(44px, 1fr));
  gap: 6px;
}
.aside-btn {
  aspect-ratio: 1;
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: var(--r-sm);
  cursor: pointer;
  font: 500 var(--fs-body) / 1 var(--font-mono);
  color: var(--fg-2);
  transition: all var(--dur-fast) var(--ease-out);
  display: grid;
  place-items: center;
}
.aside-btn:hover {
  border-color: var(--sky);
  color: var(--sky-active);
}
.aside-btn.answered {
  background: var(--sky);
  color: white;
  border-color: var(--sky);
}
.aside-btn.current {
  background: var(--surface);
  color: var(--sunrise-strong);
  border-color: var(--sunrise);
  font-weight: var(--fw-bold);
  box-shadow: 0 0 0 2px var(--sunrise-soft);
}

.nav-buttons {
  display: flex;
  gap: var(--s-3);
  margin-top: var(--s-4);
  justify-content: space-between;
}

/* 顶部总进度条 */
.play-progress {
  width: 200px;
  margin: 0 var(--s-4);
}

.empty-tip {
  text-align: center;
  padding: var(--s-12) var(--s-5);
  color: var(--muted);
  font-size: var(--fs-body-lg);
}

/* --- 移动端断点 --- */

@media (max-width: 900px) {
  .play-body {
    flex-direction: column;
    padding: var(--s-3);
  }
  .play-aside {
    width: 100%;
    position: static;
  }
  .play-progress { width: 100%; }
}

@media (max-width: 640px) {
  .play-header {
    padding: var(--s-2) var(--s-3);
    gap: var(--s-2);
  }
  .header-left {
    font-size: var(--fs-body);
    gap: var(--s-2);
  }
  .timer-display {
    font-size: var(--fs-h3);
    padding: 4px var(--s-2);
  }
  .play-body {
    padding: var(--s-2);
  }
  .play-aside {
    padding: var(--s-3);
  }
  .aside-grid {
    gap: 4px;
  }
  .aside-btn {
    font-size: var(--fs-caption);
  }
  .nav-buttons {
    flex-direction: column;
    gap: var(--s-2);
  }
  .nav-buttons .el-button {
    width: 100%;
  }
}
</style>
