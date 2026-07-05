<script setup lang="ts">
/**
 * 单题渲染组件 — 答题页和成绩页共用。
 *
 * Props:
 *   question       — 题目数据
 *   userAnswer     — 学员答案（未填时为 undefined）
 *   readonly       — true 时不响应输入（用于结果页展示）
 *   showCorrect    — true 时显示正确答案 + 评语（结果页）
 *   correctAnswer  — 正确答案（showCorrect=true 时）
 *   comment        — 评语（showCorrect=true 时）
 *
 * Emits:
 *   update:answer  — 学员修改答案（question_id, new value）
 */
import { computed } from 'vue';
import type { QuestionPublic, QuestionType } from '@/types/api';
import { formatChapterCode } from '@/utils/formatChapterCode';
import { renderMarkdown } from '@/composables/useMarkdown';

interface Props {
  question: QuestionPublic;
  userAnswer?: string;
  readonly?: boolean;
  showCorrect?: boolean;
  correctAnswer?: string;
  comment?: string;
}

const props = withDefaults(defineProps<Props>(), {
  userAnswer: '',
  readonly: false,
  showCorrect: false,
  correctAnswer: '',
  comment: '',
});

const emit = defineEmits<{
  (e: 'update:answer', questionId: number, value: string): void;
}>();

/** 题型 label。 */
const typeLabel = computed(() => {
  const map: Record<QuestionType, string> = {
    single: '单选',
    multi: '多选',
    judge: '判断',
    calc: '计算',
    comprehensive: '综合',
    // fix-30b: 5 题型 → 7 题型（补全 Record 完整性，避免 vue-tsc 报错）
    short_answer: '简答',
    case_analysis: '案例分析',
  };
  return map[props.question.type];
});

/** 难度 label。 */
const difficultyLabel = computed(() => {
  return ['', '简单', '中等', '困难'][props.question.difficulty] || '';
});

/** 选项字母 A/B/C/D/...。 */
const optionLetters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

/** 学员答案（按题型归一为集合）。 */
const userSet = computed(() => {
  return new Set((props.userAnswer || '').toUpperCase().split('').filter(Boolean));
});

/** 切选项（单选 / 多选）。 */
function toggleOption(letter: string): void {
  if (props.readonly) return;
  if (props.question.type === 'single' || props.question.type === 'judge') {
    emit('update:answer', props.question.id, letter);
  } else if (props.question.type === 'multi') {
    const next = new Set(userSet.value);
    if (next.has(letter)) {
      next.delete(letter);
    } else {
      next.add(letter);
    }
    emit('update:answer', props.question.id, Array.from(next).sort().join(''));
  }
}

/**
 * 主观题输入处理 — Element Plus 的 @update:model-value 参数是 value 字符串。
 *
 * 之前用 `@input="onTextInput(e: Event)"` 错误地把字符串当 Event 处理，
 * 导致 `e.target` 为 undefined、`e.target.value` 抛 TypeError。
 * 修复方案：监听 `@update:model-value`，直接接 value 字符串。
 */
function onTextInput(value: string): void {
  if (props.readonly) return;
  emit('update:answer', props.question.id, value ?? '');
}

/** 判断题：对/错。 */
const judgeOptions = ['对', '错'];

/**
 * 题目是否主观（计算/综合/简答/案例分析）。
 * 结果页判定需要这个,因为主观题不显示"正确/错误"对错标记。
 */
const isSubjective = computed(() => {
  const t = props.question.type;
  return t === 'calc' || t === 'comprehensive' || t === 'short_answer' || t === 'case_analysis';
});

/**
 * fix-30b:case_analysis 答案解析。
 *
 * 提交时构造 `{sub_answers: {sq_id: text}, conclusion: text}` JSON 字符串。
 * store 收到的 user_answer 是 JSON string,grader 端按题型 parse。
 */
interface CaseAnswer {
  sub_answers: Record<string, string>;
  conclusion: string;
}

/** 把 userAnswer(JSON 字符串)反序列化为结构 — 解析失败时返回空结构。 */
const parsedCaseAnswer = computed<CaseAnswer>(() => {
  if (props.question.type !== 'case_analysis') {
    return { sub_answers: {}, conclusion: '' };
  }
  try {
    const raw = props.userAnswer ?? '';
    if (!raw) return { sub_answers: {}, conclusion: '' };
    const parsed = JSON.parse(raw) as Partial<CaseAnswer>;
    return {
      sub_answers: parsed.sub_answers ?? {},
      conclusion: parsed.conclusion ?? '',
    };
  } catch {
    // 历史脏数据(用户曾以字符串形式输入过)兜底
    return { sub_answers: {}, conclusion: '' };
  }
});

/**
 * 子问题作答回调 — 把当前所有子问题答案 + conclusion 重新组装为 JSON 字符串 emit。
 */
function onSubQuestionChange(sqId: string, value: string): void {
  if (props.readonly) return;
  const next: CaseAnswer = {
    ...parsedCaseAnswer.value,
    sub_answers: { ...parsedCaseAnswer.value.sub_answers, [sqId]: value ?? '' },
  };
  emit('update:answer', props.question.id, JSON.stringify(next));
}

/** conclusion 变更回调。 */
function onConclusionChange(value: string): void {
  if (props.readonly) return;
  const next: CaseAnswer = {
    ...parsedCaseAnswer.value,
    conclusion: value ?? '',
  };
  emit('update:answer', props.question.id, JSON.stringify(next));
}

/**
 * 简答题关键要点提示 — 从 rubric 提取(无 rubric 时不显示)。
 * case_analysis 也可复用,只在子问题标题旁展示。
 */
const keyPointsHint = computed(() => {
  const r = props.question.rubric;
  if (!r) return '';
  // 简答题:所有 sub_questions key_points 取并集;无 sub_questions 时取 conclusion criteria
  const points: string[] = [];
  for (const sq of r.sub_questions) {
    points.push(...sq.key_points);
  }
  if (points.length === 0) {
    points.push(...r.conclusion.criteria);
  }
  return points.slice(0, 5).join('；');
});

/**
 * 是否 AI 改编题（exp-1 修复，前端仅显示标识）。
 * 后端 QuestionPublic.is_adapted 仅混合模式 (mode='mixed') 改编题为 true；
 * 标准模式 / 原题均为 undefined / false。
 */
const isAdapted = computed(() => Boolean(props.question.is_adapted));

/**
 * AI 改编 tooltip 文案 — 包含 source_question_id 让用户能追溯到源原题。
 * 后端 source_question_id 缺失时降级为通用提示。
 */
const adaptedTooltip = computed(() => {
  const src = props.question.source_question_id;
  if (src && src !== props.question.id) {
    return `本题目由 AI 基于原题 #${src} 改编（保留原考点 + 答案等价）`;
  }
  return '本题目由 AI 基于原题改编（保留原考点 + 答案等价）';
});

/** 判分结果（结果页用）。 */
const isCorrect = computed(() => {
  if (!props.showCorrect) return null;
  if (isSubjective.value) {
    // 主观题用 awarded_score > 0 判定（外部传入；这里只看正确性）
    return null;
  }
  const u = (props.userAnswer || '').toUpperCase().replace(/[^A-Z]/g, '');
  const c = (props.correctAnswer || '').toUpperCase().replace(/[^A-Z]/g, '');
  return u === c && u.length > 0;
});
</script>

<template>
  <div class="qcard">
    <div class="qcard-header">
      <el-tag size="small" :type="question.type === 'single' ? 'success' : question.type === 'multi' ? 'warning' : question.type === 'judge' ? 'info' : 'danger'">
        {{ typeLabel }}
      </el-tag>
      <el-tag size="small" effect="plain">难度: {{ difficultyLabel }}</el-tag>
      <el-tag size="small" effect="plain" type="info">第 {{ question.sequence }} 题</el-tag>
      <el-tag size="small" type="info">分值: {{ question.score }} 分</el-tag>
      <!-- exp-1：章节标识（后端 chapter_code 已透传，前端首次显示） -->
      <el-tag
        v-if="question.chapter_code"
        size="small"
        type="info"
        effect="plain"
        class="chapter-tag"
      >
        {{ formatChapterCode(question.chapter_code) }}
      </el-tag>
      <!-- exp-1：AI 改编徽章 — 仅混合模式改编题显示 -->
      <el-tooltip
        v-if="isAdapted"
        :content="adaptedTooltip"
        placement="top"
      >
        <el-tag
          type="primary"
          size="small"
          effect="dark"
          class="ai-badge"
          data-testid="ai-adapted-badge"
        >
          AI 改编
        </el-tag>
      </el-tooltip>
      <span v-if="showCorrect && isCorrect === true" class="answer-correct">✓ 正确</span>
      <span v-else-if="showCorrect && isCorrect === false" class="answer-wrong">✗ 错误</span>
    </div>

    <div class="question-stem" v-html="renderMarkdown(question.stem)" />

    <!-- 客观题：单选 / 多选 -->
    <div v-if="question.type === 'single' || question.type === 'multi'" class="options-list">
      <div
        v-for="(opt, idx) in question.options || []"
        :key="idx"
        class="answer-option"
        :class="{ selected: userSet.has(optionLetters[idx]), readonly }"
        @click="toggleOption(optionLetters[idx])"
      >
        <strong>{{ optionLetters[idx] }}.</strong> {{ opt }}
      </div>
    </div>

    <!-- 判断题 -->
    <div v-else-if="question.type === 'judge'" class="options-list">
      <div
        v-for="(opt, idx) in judgeOptions"
        :key="opt"
        class="answer-option"
        :class="{ selected: userAnswer === (idx === 0 ? '对' : '错'), readonly }"
        @click="toggleOption(idx === 0 ? '对' : '错')"
      >
        {{ opt }}
      </div>
    </div>

    <!-- 主观题：计算 / 综合 -->
    <div v-else-if="question.type === 'calc' || question.type === 'comprehensive'">
      <el-input
        type="textarea"
        :model-value="userAnswer ?? ''"
        :readonly="readonly"
        :rows="6"
        placeholder="请按 '1.xxx；2.xxx；...' 格式分小问作答（按习惯写也行，自动识别）"
        @update:model-value="onTextInput"
      />
    </div>

    <!-- 主观题：简答题（fix-30b：textarea + 字数提示 + 关键点评分提示） -->
    <div v-else-if="question.type === 'short_answer'">
      <div data-testid="short-answer-input">
        <el-input
          type="textarea"
          :model-value="userAnswer ?? ''"
          :readonly="readonly"
          :rows="4"
          maxlength="1000"
          show-word-limit
          placeholder="请简要作答（建议 100-300 字）"
          @update:model-value="onTextInput"
        />
      </div>
      <small v-if="keyPointsHint" class="keypoints-hint">
        💡 提示：答案应覆盖关键点 — {{ keyPointsHint }}
      </small>
    </div>

    <!-- 主观题：案例分析（fix-30b：多子问题 textarea + conclusion textarea） -->
    <div v-else-if="question.type === 'case_analysis'" class="case-analysis">
      <div
        v-for="sq in (question.rubric?.sub_questions ?? [])"
        :key="sq.id"
        class="sub-question"
        :data-testid="`case-sub-${sq.id}`"
      >
        <h4 class="sub-q-title">
          子问题 {{ sq.id }}
          <span class="sub-q-points">({{ sq.points }} 分)</span>
        </h4>
        <el-input
          type="textarea"
          :model-value="parsedCaseAnswer.sub_answers[sq.id] ?? ''"
          :readonly="readonly"
          :rows="6"
          maxlength="1500"
          show-word-limit
          :placeholder="`作答子问题 ${sq.id}...`"
          @update:model-value="(v: string) => onSubQuestionChange(sq.id, v)"
        />
        <small v-if="sq.key_points.length" class="keypoints-hint">
          💡 关键点: {{ sq.key_points.join('；') }}
        </small>
      </div>
      <div v-if="question.rubric?.conclusion" class="sub-question" data-testid="case-conclusion">
        <h4 class="sub-q-title">
          结论
          <span class="sub-q-points">({{ question.rubric.conclusion.points }} 分)</span>
        </h4>
        <el-input
          type="textarea"
          :model-value="parsedCaseAnswer.conclusion"
          :readonly="readonly"
          :rows="4"
          maxlength="800"
          show-word-limit
          placeholder="请给出综合结论..."
          @update:model-value="onConclusionChange"
        />
      </div>
    </div>

    <!-- 结果页：正确答案 + 评语 -->
    <div v-if="showCorrect" class="result-section">
      <div class="result-line">
        <span class="result-label">📌 参考答案：</span>
        <span class="result-value">{{ correctAnswer }}</span>
      </div>
      <div v-if="comment" class="result-line">
        <span class="result-label">💬 评语：</span>
        <span class="result-value">{{ comment }}</span>
      </div>
      <div v-if="!isSubjective" class="result-line">
        <span class="result-label">📝 你的答案：</span>
        <span class="result-value" :class="isCorrect ? 'answer-correct' : 'answer-wrong'">
          {{ userAnswer || '（未作答）' }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 题目卡（设计令牌化 — 与 global.css 的 .qcard 对齐） */
.qcard {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: var(--s-8);
  margin-bottom: var(--s-4);
  box-shadow: var(--shadow-md);
}
.qcard-header {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s-2);
  align-items: center;
  margin-bottom: var(--s-5);
  padding-bottom: var(--s-3);
  border-bottom: 1px dashed var(--border-soft);
}
.qcard-header :deep(.el-tag) {
  border-radius: var(--r-sm);
  font-weight: var(--fw-medium);
}

/* exp-1：章节标识 — 不抢眼，仅作辅助 meta（effect=plain 蓝色） */
.chapter-tag {
  letter-spacing: 0.02em;
}

/* exp-1：AI 改编徽章 — 深色 primary 强视觉锚点，仅混合模式改编题 */
.ai-badge {
  margin-left: auto;  /* 推到 header 右侧，与 answer-correct/wrong 对齐 */
  font-weight: var(--fw-semibold);
  letter-spacing: 0.02em;
}

.question-stem {
  font: 600 22px / 1.5 var(--font-display);
  color: var(--fg);
  margin-bottom: var(--s-6);
  letter-spacing: -0.005em;
  white-space: pre-wrap;
  /* fix-30: GFM 表格/代码块需自动换行,不强制保留 markdown 原文 \n */
}

/* fix-30: question-stem 内的 GFM markdown 表格/列表/粗体样式 — 走 v-html 后无内置样式 */
.question-stem :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: var(--s-3) 0;
  font-size: 18px;
  font-weight: 400;
}
.question-stem :deep(th),
.question-stem :deep(td) {
  border: 1px solid var(--border);
  padding: var(--s-2) var(--s-4);
  text-align: left;
  vertical-align: top;
}
.question-stem :deep(th) {
  background: var(--surface-2);
  font-weight: var(--fw-semibold);
  color: var(--fg);
}
.question-stem :deep(tr:nth-child(even) td) {
  background: var(--surface-soft, var(--surface-2));
}
.question-stem :deep(ul),
.question-stem :deep(ol) {
  margin: var(--s-3) 0;
  padding-left: var(--s-6);
}
.question-stem :deep(li) {
  margin-bottom: var(--s-2);
}
.question-stem :deep(strong) {
  font-weight: var(--fw-semibold);
  color: var(--fg);
}
.question-stem :deep(code) {
  font-family: var(--font-mono);
  background: var(--surface-2);
  padding: 1px var(--s-2);
  border-radius: var(--r-sm);
  font-size: 18px;
}
.question-stem :deep(pre) {
  background: var(--surface-2);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--r-md);
  overflow-x: auto;
  font-family: var(--font-mono);
}

/* 选项：4/5 个选项 + selected/correct/wrong 态（与 global .option 对齐） */
.options-list { display: flex; flex-direction: column; }

.answer-option {
  display: flex;
  align-items: center;
  gap: var(--s-4);
  padding: var(--s-4) var(--s-5);
  border: 1.5px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface);
  cursor: pointer;
  margin-bottom: var(--s-3);
  transition: all var(--dur-fast) var(--ease-out);
  font-size: var(--fs-body-lg);
  color: var(--fg);
}
.answer-option:hover {
  border-color: var(--sky-fog);
  background: var(--sky-soft);
}
.answer-option.selected {
  border-color: var(--sky);
  background: var(--sky-soft);
  font-weight: var(--fw-medium);
}
.answer-option.readonly { cursor: default; }
.answer-option.readonly.selected { background: var(--sky-soft); }

/* 结果页 */
.result-section {
  margin-top: var(--s-5);
  padding: var(--s-4) var(--s-5);
  background: var(--sky-soft);
  border-radius: var(--r-md);
  border-left: 3px solid var(--sky);
}
.result-line {
  font-size: var(--fs-body);
  line-height: var(--lh-snug);
  color: var(--fg);
  margin-bottom: var(--s-2);
}
.result-label {
  font-weight: var(--fw-semibold);
  margin-right: var(--s-2);
  color: var(--sky-active);
}
.result-value { color: var(--fg-2); }

.answer-correct { color: var(--success); font-weight: var(--fw-semibold); }
.answer-wrong   { color: var(--danger);  font-weight: var(--fw-semibold); }

/* fix-30b：主观题提示与子问题块 */
.keypoints-hint {
  display: block;
  margin-top: var(--s-2);
  color: var(--muted);
  font-size: var(--fs-caption);
  background: var(--surface-2);
  padding: var(--s-2) var(--s-3);
  border-radius: var(--r-sm);
  border-left: 3px solid var(--sky);
}

.case-analysis {
  display: flex;
  flex-direction: column;
  gap: var(--s-5);
}
.sub-question {
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-md);
  padding: var(--s-4) var(--s-5);
}
.sub-q-title {
  margin: 0 0 var(--s-3);
  font: var(--fw-semibold) var(--fs-body-lg) / 1.3 var(--font-display);
  color: var(--fg);
  display: flex;
  align-items: baseline;
  gap: var(--s-2);
}
.sub-q-points {
  font: 400 var(--fs-caption) / 1 var(--font-mono);
  color: var(--sky-active);
  font-weight: var(--fw-medium);
}

@media (max-width: 720px) {
  .qcard { padding: var(--s-5) var(--s-4); }
  .question-stem { font-size: 20px; }
}
</style>
