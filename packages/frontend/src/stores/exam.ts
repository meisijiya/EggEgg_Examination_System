/**
 * 考试 store — 当前考试的状态：题目 / 答案 / 计时 / 草稿持久化。
 *
 * 设计：
 * - 启动考试时拉取试卷，缓存题目到 store
 * - 答案变更时自动同步到 localStorage（每 30s 兜底再写一次）
 * - 计时器：本地 1s 间隔；与 server 启动时间比较得出"剩余秒数"
 * - 提交后清草稿 + 清 store
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import {
  getExam,
  startExam,
  submitExam as apiSubmitExam,
} from '@/api';
import { TOKEN_KEY } from '@/api/client';
import type {
  QuestionPublic,
  StartExamResponse,
  SubmitExamResponse,
  Subject,
} from '@/types/api';

/** localStorage 中草稿的 key（按 attempt_id 区分）。 */
function draftKey(attemptId: number): string {
  return `fes_draft_${attemptId}`;
}

/** localStorage 中当前选中 subject 的 key。 */
const CURRENT_SUBJECT_KEY = 'fes_current_subject';

export const useExamStore = defineStore('exam', () => {
  // ---------- state ----------
  const attemptId = ref<number | null>(null);
  const startedAt = ref<string>('');
  const timeLimitMinutes = ref<number>(120);
  const totalScore = ref<number>(0);
  const questions = ref<QuestionPublic[]>([]);
  /** 学员答案 — question_id → answer string */
  const answers = ref<Record<number, string>>({});
  /** 当前激活题目的 sequence（用于卡片切题） */
  const currentSequence = ref<number>(1);
  /** 已提交结果缓存（result 页用） */
  const lastResult = ref<SubmitExamResponse | null>(null);
  /**
   * fix-30a:当前选中科目 — 顶栏 SubjectSwitcher 写入,startNew 时读出。
   * 持久化到 localStorage,刷新后保留用户上次选择。
   */
  const currentSubject = ref<Subject | null>(null);

  // ---------- 初始化:从 localStorage 恢复 subject ----------
  try {
    const raw = localStorage.getItem(CURRENT_SUBJECT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Subject;
      if (parsed && typeof parsed.id === 'string' && typeof parsed.name === 'string') {
        currentSubject.value = parsed;
      }
    }
  } catch {
    // 静默 — 解析失败就用 null,UI 走兜底
  }

  // ---------- getters ----------

  /** 已答题数（user_answer 非空）。 */
  const answeredCount = computed(() => {
    return Object.values(answers.value).filter((a) => a && a.length > 0)
      .length;
  });

  /** 总题数。 */
  const totalQuestions = computed(() => questions.value.length);

  /** 按题型分组的题目。 */
  const questionsByType = computed(() => {
    const map: Record<string, QuestionPublic[]> = {};
    for (const q of questions.value) {
      if (!map[q.type]) map[q.type] = [];
      map[q.type].push(q);
    }
    return map;
  });

  /** 当前显示的题目。 */
  const currentQuestion = computed(() => {
    return questions.value.find((q) => q.sequence === currentSequence.value) || null;
  });

  // ---------- actions ----------

  /**
   * 启动一次新考试（POST /exams/start）。
   *
   * 参数:
   *   subjectId — fix-30a 多科目隔离;默认读 store.currentSubject.id
   *   mode: 'standard'（默认）/ 'mixed' — 转发给后端
   *
   * 防御:启动前校验 localStorage 中有 token,避免用空 token 发起请求
   * 后拿到 401 导致 axios 拦截器强制刷跳 /login(用户体验差,而且
   * mixed 模式 ~30s 启动会让"以为登录失败"更突出)。
   */
  async function startNew(
    mode: 'standard' | 'mixed' = 'standard',
    subjectId?: string,
  ): Promise<StartExamResponse> {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      throw new Error('登录已过期，请重新登录');
    }
    // ponytail: 优先用显式参数,其次 store 状态,再不行兜底为 'fin-mgmt'
    // 让 fix-30a 后端未就绪时不至于 422
    const sid = subjectId ?? currentSubject.value?.id ?? 'fin-mgmt';
    const resp = await startExam(sid, mode);
    applyStartResponse(resp);
    return resp;
  }

  /**
   * 设置当前选中科目 — 顶栏 SubjectSwitcher 触发,持久化到 localStorage。
   */
  function setSubject(s: Subject): void {
    currentSubject.value = s;
    try {
      localStorage.setItem(CURRENT_SUBJECT_KEY, JSON.stringify(s));
    } catch {
      // 静默 — 隐私模式 / quota 满不影响当前会话
    }
  }

  /**
   * 清空当前科目 — logout / 切换失败时调用。
   */
  function clearSubject(): void {
    currentSubject.value = null;
    try {
      localStorage.removeItem(CURRENT_SUBJECT_KEY);
    } catch {
      // 静默
    }
  }

  /**
   * 把 StartExamResponse 写入 store（用于断线重连场景）。
   */
  function applyStartResponse(resp: StartExamResponse): void {
    attemptId.value = resp.attempt_id;
    startedAt.value = resp.started_at;
    timeLimitMinutes.value = resp.time_limit_minutes;
    totalScore.value = resp.total_score;
    questions.value = resp.questions;
    answers.value = {};
    currentSequence.value = 1;
    lastResult.value = null;
    // 启动时尝试恢复草稿
    const draft = loadDraft(resp.attempt_id);
    if (draft) {
      answers.value = { ...draft };
    }
    saveDraft();
  }

  /**
   * 加载已存在的 attempt（用于断线重连 / F5 刷新）。
   */
  async function loadExisting(attemptIdParam: number): Promise<void> {
    const snap = await getExam(attemptIdParam);
    attemptId.value = snap.attempt_id;
    startedAt.value = snap.started_at;
    timeLimitMinutes.value = snap.time_limit_minutes;
    questions.value = snap.questions;
    answers.value = { ...snap.answers };
    // 计算 totalScore（按 spec = 110，但用后端给的更稳）
    totalScore.value = questions.value.reduce((s, q) => s + q.score, 0);
    currentSequence.value = 1;
    lastResult.value = null;
  }

  /**
   * 写入单题答案（不立即持久化 — 30s 兜底 + 提交时持久化）。
   *
   * undefined-safe：value 是 undefined/null 时归一为空串，
   * 避免下游 `answers[questionId].length` 之类访问抛错。
   */
  function setAnswer(questionId: number, value: string | undefined | null): void {
    const safe = value ?? '';
    answers.value = { ...answers.value, [questionId]: safe };
  }

  /**
   * 切到指定题（sequence）。
   */
  function goToQuestion(sequence: number): void {
    if (sequence < 1 || sequence > totalQuestions.value) return;
    currentSequence.value = sequence;
  }

  /**
   * 提交考试（POST /exams/{id}/submit）。
   */
  async function submit(): Promise<SubmitExamResponse> {
    if (attemptId.value === null) {
      throw new Error('未启动考试');
    }
    const payload = {
      answers: questions.value.map((q) => ({
        question_id: q.id,
        user_answer: answers.value[q.id] ?? '',
      })),
    };
    const resp = await apiSubmitExam(attemptId.value, payload);
    lastResult.value = resp;
    clearDraft();
    return resp;
  }

  /**
   * 草稿持久化到 localStorage。
   */
  function saveDraft(): void {
    if (attemptId.value === null) return;
    try {
      localStorage.setItem(
        draftKey(attemptId.value),
        JSON.stringify(answers.value),
      );
    } catch (e) {
      // localStorage 满 / 隐私模式 — 静默忽略
      console.warn('保存草稿失败', e);
    }
  }

  /**
   * 从 localStorage 读取草稿。
   */
  function loadDraft(id: number): Record<number, string> | null {
    try {
      const raw = localStorage.getItem(draftKey(id));
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Record<string, string>;
      const out: Record<number, string> = {};
      for (const [k, v] of Object.entries(parsed)) {
        out[Number(k)] = v;
      }
      return out;
    } catch {
      return null;
    }
  }

  /**
   * 清除草稿。
   */
  function clearDraft(): void {
    if (attemptId.value === null) return;
    try {
      localStorage.removeItem(draftKey(attemptId.value));
    } catch {
      // 静默
    }
  }

  /**
   * 计时器辅助 — 计算考试截止时间戳（ms）。
   *
   * 注意：server 启动时间用 ISO 字符串，本地解析为 Date。客户端时钟与 server 略有偏差，
   * 但 120 分钟容忍秒级误差。
   */
  function deadlineMs(): number {
    if (!startedAt.value) return 0;
    const startMs = new Date(startedAt.value).getTime();
    return startMs + timeLimitMinutes.value * 60_000;
  }

  /**
   * 重置整个 store（用于交卷后或重新开始）。
   * 注意:不重置 currentSubject — 用户的科目选择跨考试保持。
   */
  function reset(): void {
    attemptId.value = null;
    startedAt.value = '';
    timeLimitMinutes.value = 120;
    totalScore.value = 0;
    questions.value = [];
    answers.value = {};
    currentSequence.value = 1;
    lastResult.value = null;
  }

  return {
    // state
    attemptId,
    startedAt,
    timeLimitMinutes,
    totalScore,
    questions,
    answers,
    currentSequence,
    lastResult,
    currentSubject,
    // getters
    answeredCount,
    totalQuestions,
    questionsByType,
    currentQuestion,
    // actions
    startNew,
    setSubject,
    clearSubject,
    applyStartResponse,
    loadExisting,
    setAnswer,
    goToQuestion,
    submit,
    saveDraft,
    loadDraft,
    clearDraft,
    deadlineMs,
    reset,
  };
});
