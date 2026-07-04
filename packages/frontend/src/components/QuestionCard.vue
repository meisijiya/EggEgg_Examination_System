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

/** 题目是否主观（计算/综合）。 */
const isSubjective = computed(() => {
  return props.question.type === 'calc' || props.question.type === 'comprehensive';
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
      <span v-if="showCorrect && isCorrect === true" class="answer-correct">✓ 正确</span>
      <span v-else-if="showCorrect && isCorrect === false" class="answer-wrong">✗ 错误</span>
    </div>

    <div class="question-stem">{{ question.stem }}</div>

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
    <div v-else>
      <el-input
        type="textarea"
        :model-value="userAnswer ?? ''"
        :readonly="readonly"
        :rows="6"
        placeholder="请按 '1.xxx；2.xxx；...' 格式分小问作答（按习惯写也行，自动识别）"
        @update:model-value="onTextInput"
      />
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

.question-stem {
  font: 600 22px / 1.5 var(--font-display);
  color: var(--fg);
  margin-bottom: var(--s-6);
  letter-spacing: -0.005em;
  white-space: pre-wrap;
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

@media (max-width: 720px) {
  .qcard { padding: var(--s-5) var(--s-4); }
  .question-stem { font-size: 20px; }
}
</style>
