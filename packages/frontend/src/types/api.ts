/**
 * 题型 / 难度 / 角色类型 — 与后端 Pydantic schema 严格对应。
 */
export type QuestionType =
  | 'single'
  | 'multi'
  | 'judge'
  | 'calc'
  | 'comprehensive';

export type DifficultyLevel = 1 | 2 | 3;

export type UserRole = 'user' | 'admin';

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
 * 客户端错误类型 — axios error 包装。
 */
export interface ApiError {
  status: number;
  message: string;
}
