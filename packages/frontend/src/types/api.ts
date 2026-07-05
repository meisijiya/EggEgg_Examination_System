/**
 * 题型 / 难度 / 角色类型 — 与后端 Pydantic schema 严格对应。
 *
 * fix-30b 扩展:增加 short_answer / case_analysis,5 题型 → 7 题型。
 * 旧 5 种保持兼容(财务管理员侧已有逻辑不动),新 2 种由 QuestionCard 渲染分支处理。
 */
export type QuestionType =
  | 'single'
  | 'multi'
  | 'judge'
  | 'calc'
  | 'comprehensive'
  | 'short_answer'
  | 'case_analysis';

export type DifficultyLevel = 1 | 2 | 3;

export type UserRole = 'user' | 'admin';

/**
 * 考试科目 — fix-30a 后端 /api/subjects 返回的字典项。
 *
 * id 暂用字面量('fin-mgmt' / 'corp-strat'),便于前端 import 与编译期校验;
 * 后端若改为自增 ID,这里同步放宽为 string。
 */
export interface Subject {
  id: string;
  name: string;
  question_count?: number;
}

/**
 * 登录请求 / 响应。
 */
export interface LoginRequest {
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
  role: UserRole;
  expires_in: number;
}

/**
 * 主观题评分量规(case_analysis / 复杂综合题专用) — fix-30b 由后端 grader 扩展时填充。
 *
 * 子问题结构:每个子问题有 id / 分值 / 关键要点 / 权重。
 * conclusion 用于案例分析末尾的总结性论述。
 */
export interface QuestionRubric {
  sub_questions: Array<{
    id: string;
    points: number;
    key_points: string[];
    weight: number;
  }>;
  conclusion: { points: number; criteria: string[] };
}

/**
 * 题目 — 学员视图（隐藏 answer / key_points / analysis）。
 */
export interface QuestionPublic {
  id: number;
  type: QuestionType;
  chapter_id: number;
  chapter_code: string;
  difficulty: DifficultyLevel;
  stem: string;
  options: string[] | null;
  score: number;
  sequence: number;
  /**
   * fix-22 P0 + exp-1: AI 改编标记 — 仅混合模式 (mode='mixed') 改编题为 true。
   * 后端 `assemble_paper_async` 在 mixed 模式下对 ~30% 题改编后会设置；
   * 标准模式 / 未改编题为 false（前端 UI 标注 + tooltip 用）。
   */
  is_adapted?: boolean;
  /**
   * 改编源原题 ID（traceability）。
   * - 改编题：= 源原题 id（一般 = question.id，但前端不必假设相等）
   * - 原题 / standard 模式：undefined
   */
  source_question_id?: number;
  /**
   * fix-30b:case_analysis / 综合大题的子问题评分结构。无 rubric 时按整题文本作答。
   */
  rubric?: QuestionRubric;
}

/**
 * 启动考试请求 — fix-30a 强制要求 subject_id(多科目隔离)。
 */
export interface StartExamRequest {
  subject_id: string;
  mode: 'standard' | 'mixed';
}

/**
 * 启动考试响应。
 */
export interface StartExamResponse {
  attempt_id: number;
  started_at: string;
  time_limit_minutes: number;
  questions: QuestionPublic[];
  total_score: number;
}

/**
 * 考试快照（断线重连用）。
 */
export interface ExamSnapshot {
  attempt_id: number;
  started_at: string;
  time_limit_minutes: number;
  submitted_at: string | null;
  questions: QuestionPublic[];
  answers: Record<number, string>;
}

/**
 * 提交答案的单题条目。
 */
export interface SubmitAnswerItem {
  question_id: number;
  user_answer: string;
}

/**
 * 提交考试请求。
 */
export interface SubmitExamRequest {
  answers: SubmitAnswerItem[];
}

/**
 * 单题判分详情。
 */
export interface GradedAnswerDetail {
  question_id: number;
  sequence: number;
  type: QuestionType;
  chapter_code: string;
  stem: string;
  user_answer: string;
  correct_answer: string;
  is_correct: boolean | null;
  awarded_score: number;
  full_score: number;
  comment: string;
  /** 主观题扩展：识别到的分小问作答数（≥ 2 时填，客观题为 undefined）。 */
  sub_answer_count?: number;
  /** 主观题扩展：未覆盖关键要点（最多 3 条，仅部分覆盖时填）。 */
  missed_points?: string[];
  /**
   * fix-23a:题目选项列表。
   * - single / multi:有值，列表中的元素按 A/B/C/D... 顺序排列
   * - judge:有值（['对', '错'] 或 ['A', 'B']）
   * - calc / comprehensive / short_answer / case_analysis:null
   *
   * 结果页渲染：单/多选显示 "A. xxx [✓]" 格式，正确选项打钩；
   * 判断显示 "对 / 错"；主观题不渲染选项。
   */
  options?: string[] | null;
}

/**
 * 交卷响应。
 */
export interface SubmitExamResponse {
  attempt_id: number;
  total_score: number;
  score_by_chapter: Record<string, number>;
  score_by_type: Record<string, number>;
  answers: GradedAnswerDetail[];
  submitted_at: string;
}

/**
 * 成绩详情（result 接口）。
 */
export interface ExamResult {
  attempt_id: number;
  started_at: string;
  submitted_at: string | null;
  total_score: number;
  score_by_chapter: Record<string, number>;
  score_by_type: Record<string, number>;
  answers: GradedAnswerDetail[];
}

/**
 * 仪表盘 — 单次成绩摘要。
 */
export interface AttemptSummary {
  attempt_id: number;
  started_at: string;
  submitted_at: string | null;
  total_score: number;
}

/**
 * 仪表盘响应。
 */
export interface DashboardResponse {
  history: AttemptSummary[];
  score_trend: number[];
  chapter_radar: Record<string, number>;
  total_attempts: number;
}

/**
 * 讲解请求 / 响应。
 */
export interface ExplainRequest {
  question_id: number;
  level: 'standard' | 'detailed';
}

export interface ExplainResponse {
  question_id: number;
  available: boolean;
  explanation: string;
  reference_answer: string;
  analysis: string | null;
}

/**
 * 健康检查响应。
 */
export interface HealthResponse {
  status: 'ok' | 'degraded';
  database: boolean;
  question_count: number | null;
  app_name: string;
}

/**
 * Admin review 队列。
 */
export interface ReviewQueueItem {
  id: number;
  type: QuestionType;
  chapter_code: string;
  difficulty: DifficultyLevel;
  stem: string;
  answer: string;
  key_points: string[] | null;
  flags: string[];
}

export interface ReviewQueueResponse {
  items: ReviewQueueItem[];
  total: number;
}

export interface ReviewUpdateRequest {
  answer?: string | null;
  key_points?: string[] | null;
  analysis?: string | null;
  difficulty?: DifficultyLevel | null;
}

export interface ReviewUpdateResponse {
  question_id: number;
  updated_fields: string[];
}

/**
 * fix-30a:Admin review queue 中单条 AI 生成题(待人工审核)。
 *
 * 与 ReviewQueueItem 区别:这里专门展示 multi-agent pipeline 的输出。
 * source_ref 用于回溯引用资料 — 用户硬约束折叠栏默认收起,需展开才看到。
 */
export interface AiGeneratedQuestion {
  id: number;
  subject_id: string;
  type: QuestionType;
  chapter_code: string;
  difficulty: DifficultyLevel;
  stem: string;
  options: string[] | null;
  generated_answer: string;
  key_points: string[];
  confidence: number;
  /** 来源引用:file = 资料文件名,paragraph_index = 段落序号,snippet = 引用片段原文。 */
  source_ref: {
    file: string;
    paragraph_index: number;
    snippet: string;
  };
  /** Agent pipeline 各 agent 的中间产物 — admin debug 用。 */
  agent_trace?: Array<{ agent: string; output: string }>;
}

export interface AiGeneratedQuestionsResponse {
  items: AiGeneratedQuestion[];
  total: number;
}

/**
 * fix-30a:Admin 拒绝 AI 生成题时的原因说明。
 */
export interface AiRejectRequest {
  reason: string;
}

/**
 * 客户端错误类型 — axios error 包装。
 */
export interface ApiError {
  status: number;
  message: string;
}
