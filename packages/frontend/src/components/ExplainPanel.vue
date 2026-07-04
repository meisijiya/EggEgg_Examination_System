<script setup lang="ts">
/**
 * AI 讲解面板 — 真实 SSE 流式展示。
 *
 * 数据流（与 `app/api/explain.py::_stream_explain_response` 对齐）：
 *   1. 打开面板 → 调 `explainQuestionStream`（fetch + ReadableStream）
 *   2. start 事件 → 进入"正在思考"态
 *   3. 多个 delta 事件 → `streamedText` 累积 LLM 原始 JSON 字符串（打字效果）
 *   4. done=true 终止事件 → 尝试 JSON.parse 累积文本
 *      - 解析成功：用 `summary` / `explanation` / `key_points` / `common_pitfalls` 填充结构化字段
 *      - 解析失败或 LLM 异常：`streamedText` 整体作为 `explanation`，end 事件的
 *        `reference_answer` / `analysis` 作为终极兜底
 *
 * 已知后端字段映射（实测）：
 *   - LLM JSON: `summary` → title；`explanation` → 正文；`key_points` → 遗漏要点列表；
 *     `common_pitfalls` → 学习建议
 *   - end 事件: `reference_answer` / `analysis`（题库兜底）
 *
 * Props:
 *   attemptId   — 当前考试 ID
 *   questionId  — 题目 ID
 *   visible     — 是否显示（外部 v-model:visible 控制）
 *
 * Emits:
 *   update:visible
 */
import { ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { explainQuestion, explainQuestionStream } from '@/api';
import type { ExplainResponse } from '@/types/api';

interface Props {
  attemptId: number;
  questionId: number;
  visible: boolean;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void;
}>();

/** 流式累积的原始 LLM 输出（一般是 JSON 字符串）。 */
const streamedText = ref<string>('');
/** 解析后的标题（LLM JSON `summary` 或固定"AI 讲解"）。 */
const title = ref<string>('');
/** 解析后的讲解正文。 */
const explanation = ref<string>('');
/** 解析后的"遗漏要点"列表（来自 LLM JSON `key_points`）。 */
const missedPoints = ref<string[]>([]);
/** 解析后的"学习建议"（来自 LLM JSON `common_pitfalls`）。 */
const studyTip = ref<string>('');
/** end 事件兜底：题库参考答案。 */
const fallbackAnswer = ref<string>('');
/** end 事件兜底：题库官方解析。 */
const fallbackAnalysis = ref<string>('');
/** 是否仍在流式（用于 cursor 动画）。 */
const streaming = ref<boolean>(false);
/** 错误信息（用户可见）。 */
const error = ref<string>('');
/** 是否进入 fallback 展示（讲解不可用）。 */
const fallback = ref<boolean>(false);

const close = (): void => {
  emit('update:visible', false);
  reset();
};

/** 重置所有状态。 */
function reset(): void {
  streamedText.value = '';
  title.value = '';
  explanation.value = '';
  missedPoints.value = [];
  studyTip.value = '';
  fallbackAnswer.value = '';
  fallbackAnalysis.value = '';
  streaming.value = false;
  error.value = '';
  fallback.value = false;
}

/**
 * 尝试把累积的 LLM 输出解析为结构化字段。
 *
 * LLM 实际输出形如：
 *   {
 *     "available": true,
 *     "summary": "...",
 *     "explanation": "...",
 *     "key_points": [...],
 *     "common_pitfalls": [...]
 *   }
 *
 * 参数:
 *   text — 累积的 delta 字符串
 *
 * 返回:
 *   true 表示解析成功并已填充字段；false 表示放弃解析（内容当作原文显示）。
 */
function tryParseLLMJson(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return false;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return false;
  }
  if (!parsed || typeof parsed !== 'object') return false;
  const obj = parsed as Record<string, unknown>;
  if (typeof obj.summary === 'string' && obj.summary) {
    title.value = obj.summary;
  }
  if (typeof obj.explanation === 'string' && obj.explanation) {
    explanation.value = obj.explanation;
  }
  if (Array.isArray(obj.key_points)) {
    missedPoints.value = obj.key_points.filter(
      (p): p is string => typeof p === 'string',
    );
  }
  if (Array.isArray(obj.common_pitfalls)) {
    const tips = obj.common_pitfalls.filter(
      (p): p is string => typeof p === 'string',
    );
    studyTip.value = tips.join('\n');
  }
  return true;
}

/**
 * 用 fallback JSON 接口（`/exams/:id/explain` 走 stub 分支）补充 `reference_answer` /
 * `analysis` — 仅在 SSE end 事件没给时填空（已有值不覆盖，避免丢失更准确信息）。
 */
async function fetchFallbackStub(): Promise<void> {
  try {
    const resp: ExplainResponse = await explainQuestion(props.attemptId, {
      question_id: props.questionId,
      level: 'standard',
    });
    if (resp.reference_answer && !fallbackAnswer.value) {
      fallbackAnswer.value = resp.reference_answer;
    }
    if (resp.analysis && !fallbackAnalysis.value) {
      fallbackAnalysis.value = resp.analysis;
    }
    if (!resp.available && !fallback.value) {
      fallback.value = true;
      title.value = '讲解暂不可用';
    }
  } catch {
    /* 兜底调用失败也无妨 — 用户至少能看 streamedText */
  }
}

/** 启动 SSE 流并消费事件。 */
async function startStream(): Promise<void> {
  reset();
  streaming.value = true;
  try {
    for await (const event of explainQuestionStream(
      props.attemptId,
      props.questionId,
      'standard',
    )) {
      const evType = event.event as string | undefined;
      if (evType === 'start') {
        /* 留白：UI 显示"正在生成讲解" */
      } else if (evType === 'delta') {
        const delta = event.delta;
        if (typeof delta === 'string' && delta.length > 0) {
          streamedText.value += delta;
        }
      } else if (event.done === true) {
        // end 事件 — 解析 + 兜底
        const available = event.available as boolean | undefined;
        const refAnswer = event.reference_answer as string | undefined;
        const analysis = event.analysis as string | undefined;
        if (refAnswer) fallbackAnswer.value = refAnswer;
        if (analysis) fallbackAnalysis.value = analysis;

        if (available === false || typeof event.error === 'string') {
          // 真实 LLM 不可用 — fallback 展示
          fallback.value = true;
          title.value = '讲解暂不可用';
          // 如果还有累积 delta，尝试解析（兜底 JSON 可能有效）
          if (streamedText.value && !tryParseLLMJson(streamedText.value)) {
            explanation.value = streamedText.value;
          }
          // 补一次 stub 调用拿 reference_answer / analysis
          await fetchFallbackStub();
        } else {
          // 正常 end：尝试解析累积 JSON
          if (!tryParseLLMJson(streamedText.value)) {
            // JSON 不完整或格式异常 — 把累积文本当原文显示
            explanation.value = streamedText.value;
          }
          if (!title.value) title.value = 'AI 讲解';
        }
        streaming.value = false;
        return;
      }
    }
    // 流意外结束（没收到 end 事件）— 兜底
    streaming.value = false;
    if (streamedText.value && !explanation.value) {
      if (!tryParseLLMJson(streamedText.value)) {
        explanation.value = streamedText.value;
      }
    }
    await fetchFallbackStub();
  } catch (e) {
    console.error('AI 讲解流式错误:', e);
    streaming.value = false;
    fallback.value = true;
    title.value = '讲解暂不可用';
    error.value = (e as { message?: string })?.message ?? '流式讲解失败';
    if (streamedText.value && !explanation.value) {
      explanation.value = streamedText.value;
    }
    await fetchFallbackStub();
    ElMessage.warning(error.value);
  }
}

/** 当面板从隐藏变为显示时，触发流式加载。 */
watch(
  () => props.visible,
  (val) => {
    if (val && props.questionId) {
      void startStream();
    }
  },
  { immediate: true },
);
</script>

<template>
  <el-drawer
    :model-value="visible"
    title="🤖 AI 讲解"
    size="60%"
    direction="rtl"
    :destroy-on-close="false"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @close="close"
  >
    <div v-if="streaming && !streamedText && !explanation" class="explain-loading">
      <el-icon class="is-loading"><i class="el-icon-loading" /></el-icon>
      <span>正在生成讲解...</span>
    </div>

    <div v-else class="explain-content">
      <h2 v-if="title" class="explain-title">{{ title }}</h2>

      <div class="explain-section">
        <div class="explain-section-label">📖 详细解析</div>
        <div class="explain-text" :class="{ streaming }">
          <span v-if="explanation">{{ explanation }}</span>
          <span v-else>{{ streamedText }}</span>
          <span v-if="streaming" class="cursor">▍</span>
        </div>
      </div>

      <div v-if="missedPoints.length > 0" class="explain-section">
        <div class="explain-section-label">⚠️ 关键要点</div>
        <ul>
          <li v-for="(p, i) in missedPoints" :key="i">{{ p }}</li>
        </ul>
      </div>

      <div v-if="studyTip" class="explain-section">
        <div class="explain-section-label">💡 易错提示</div>
        <div class="explain-tip">{{ studyTip }}</div>
      </div>

      <div v-if="fallbackAnswer" class="explain-section">
        <div class="explain-section-label">📌 参考答案</div>
        <div class="explain-ref">{{ fallbackAnswer }}</div>
      </div>

      <div v-if="fallbackAnalysis" class="explain-section">
        <div class="explain-section-label">📚 官方解析</div>
        <div class="explain-analysis">{{ fallbackAnalysis }}</div>
      </div>

      <el-alert
        v-if="fallback"
        type="info"
        :closable="false"
        title="讲解功能说明"
        description="本次讲解走 fallback 通道（DeepSeek 未配置或暂时不可达），已为你展示参考答案与官方解析。"
      />
    </div>

    <template #footer>
      <div class="explain-footer">
        <el-button @click="close">关闭</el-button>
        <el-button v-if="fallback" type="primary" @click="startStream">
          🔄 重试
        </el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
/* AI 讲解 Drawer — 柔色背景卡片、流式 cursor、fallback 分层 */
.explain-loading {
  display: flex;
  align-items: center;
  gap: var(--s-2);
  padding: var(--s-6);
  color: var(--muted);
  font-size: var(--fs-body-lg);
}

.explain-content {
  padding: 0 var(--s-2);
}

.explain-title {
  font: var(--fw-semibold) var(--fs-h2) / var(--lh-tight) var(--font-display);
  margin: 0 0 var(--s-4);
  color: var(--fg);
  letter-spacing: -0.01em;
}

.explain-section {
  margin-bottom: var(--s-5);
}

.explain-section-label {
  font: var(--fw-semibold) var(--fs-h4) / var(--lh-tight) var(--font-display);
  color: var(--sky-active);
  margin-bottom: var(--s-2);
}

.explain-text {
  background: var(--surface-2);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--r-md);
  line-height: var(--lh-base);
  white-space: pre-wrap;
  font-size: var(--fs-body-lg);
  color: var(--fg);
  min-height: 60px;
  border-left: 3px solid var(--sky);
}
.explain-text.streaming .cursor {
  display: inline-block;
  margin-left: 2px;
  animation: blink 1s infinite;
  color: var(--sky);
}
@keyframes blink {
  50% { opacity: 0; }
}

/* 易错提示：旭日系 */
.explain-tip {
  background: var(--sunrise-soft);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--r-md);
  color: var(--sunrise-strong);
  font-size: var(--fs-body);
  line-height: var(--lh-snug);
  white-space: pre-wrap;
  border-left: 3px solid var(--sunrise);
}

/* 参考答案 — success 柔和 */
.explain-ref {
  background: var(--success-soft);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--r-md);
  color: var(--success);
  font-weight: var(--fw-medium);
  border-left: 3px solid var(--success);
}

/* 官方解析 — sky 柔和 */
.explain-analysis {
  background: var(--sky-soft);
  padding: var(--s-3) var(--s-4);
  border-radius: var(--r-md);
  color: var(--sky-active);
  font-size: var(--fs-body);
  line-height: var(--lh-snug);
  white-space: pre-wrap;
  border-left: 3px solid var(--sky);
}

.explain-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--s-2);
}

ul {
  margin: var(--s-1) 0 0;
  padding-left: var(--s-5);
  color: var(--fg-2);
  line-height: var(--lh-base);
}
ul li { margin-bottom: var(--s-1); }
</style>