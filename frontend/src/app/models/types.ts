export interface QuizQuestion {
  /** Stable id from backend (required for per-question answer state). */
  id: string;
  question_text: string;
  options: string[];
  correct_answer: string;
  topic: string;
  /** definition | concept | comparison | application (API key: type) */
  type?: string | null;
  /** beginner | normal | technical */
  difficulty?: string | null;
  explanation?: string | null;
  source_section?: string | null;
}

export interface QuizGenerateResponse {
  questions: QuizQuestion[];
  total_questions: number;
  /** Backend-suggested quiz duration (seconds); 0 if unset. */
  time_limit_seconds?: number;
}

/** Emitted when the assistant finishes generating a quiz. */
export interface QuizReadyPayload {
  questions: QuizQuestion[];
  time_limit_seconds?: number;
}

/** Emitted from Quiz tab on submit / time-up (wall-clock duration for analytics). */
export interface QuizSubmitPayload {
  selectedAnswers: string[];
  durationSeconds: number;
}

/** Focused follow-up quiz from the results / post-quiz panel. */
export interface QuizMiniQuizIntent {
  count: number;
  topics: string[];
}

export interface TopicPerformance {
  topic: string;
  correct_count: number;
  wrong_count: number;
  total_attempts: number;
  success_rate: number;
  /** very_weak | weak | developing | good | strong (see backend quiz_result_pipeline) */
  status: string;
  /** True when success rate is strictly below 50% on this attempt. */
  is_weak_under_half?: boolean;
}

export interface TopicAnalysisResponse {
  topics: TopicPerformance[];
  /** Topic names with success rate < 50%. */
  topics_under_half?: string[];
  very_weak_topics?: string[];
  weak_topics: string[];
  developing_topics?: string[];
  good_topics?: string[];
  strong_topics: string[];
  /** Alias of developing_topics (50–69%) for older payloads */
  moderate_topics: string[];
}

/** Same shape as topic analysis from POST /quiz/topic-performance (lightweight scoring). */
export type SmartAnalysisResponse = TopicAnalysisResponse;

/** Per-question coaching after submit (wrong answers are rich). */
export interface QuizQuestionFeedback {
  question_index: number;
  topic: string;
  question_text?: string | null;
  is_correct: boolean;
  /** True when no option was selected at submit */
  is_unanswered?: boolean;
  selected_answer: string;
  /** Same as selected_answer when API sends computed field */
  user_answer?: string;
  correct_answer: string;
  question_type?: string | null;
  error_type?: string | null;
  confused_concepts?: string[];
  why_wrong?: string | null;
  teaching_snippet?: string | null;
  /** Four-part mistake coaching (preferred over generic why_wrong alone). */
  your_mistake?: string | null;
  why_incorrect?: string | null;
  correct_thinking?: string | null;
  correct_answer_explained?: string | null;
  hint?: string | null;
  teach_back_prompt?: string | null;
}

export interface TopicSnapshotLine {
  topic: string;
  correct_count: number;
  total_attempts: number;
  success_rate: number;
  band_label: string;
  coaching_line: string;
}

export interface QuizLearningBrief {
  headline: string;
  weak_topics: TopicSnapshotLine[];
  developing_topics: TopicSnapshotLine[];
  strong_topics: TopicSnapshotLine[];
  what_to_do_next: string[];
  mini_quiz_hint: string;
  resource_hints: string[];
  error_pattern_summary?: string;
}

/** Compact header stats for Results / Analysis (from latest attempt). */
export interface LastQuizSummary {
  correct: number;
  wrong: number;
  unanswered: number;
  totalQuestions: number;
  score: number;
  durationSeconds?: number | null;
}

/** Rolling stats when quiz attempts are persisted (DB). */
export interface UserQuizMemory {
  total_quiz_count: number;
  average_quiz_score: number | null;
  weak_topics_lifetime: string[];
  strong_topics_lifetime: string[];
}

export interface QuizSubmissionRequest {
  questions: QuizQuestion[];
  selected_answers: string[];
  user_id: number;
  /** "en" | "tr" — coach message and future localized strings */
  locale?: string;
  /** Active PDF session when quiz was taken (scopes attempts per material). */
  document_id?: string | null;
  duration_seconds?: number | null;
  quiz_source?: "pdf_session" | "text_summary" | null;
}

export interface QuizSubmissionResponse {
  attempt_id?: number | null;
  total_correct: number;
  total_wrong: number;
  total_unanswered?: number;
  total_question_count?: number;
  total_duration_seconds?: number | null;
  quiz_source?: string | null;
  score_percentage: number;
  performance_comment?: string | null;
  recommended_mini_quiz_count?: number;
  learning_brief?: QuizLearningBrief | null;
  topic_analysis: TopicAnalysisResponse;
  question_feedback?: QuizQuestionFeedback[];
  /** Server count of incorrect rows (compare to total_wrong). */
  listed_wrong_count?: number;
  /** Incorrect-only slice; prefer this for cards when length matches total_wrong. */
  wrong_items?: QuizQuestionFeedback[];
  /** very_weak + weak topic labels for this attempt. */
  weak_topics?: string[];
  /** Suggested focus for the next mini quiz. */
  suggested_mini_quiz_topic?: string | null;
  error_type_summary?: Record<string, number>;
  confused_topics_ranked?: string[];
  follow_up_actions?: string[];
  user_quiz_memory?: UserQuizMemory | null;
  teach_back_tasks?: string[];
}

export interface SuggestionAction {
  action: string; // "review" | "mini_quiz" | "new_quiz" | "pomodoro" | "resource" | "maintenance"
  message: string;
  pomodoro_minutes?: number | null;
  resource_query?: string | null;
}

export interface TopicSuggestion {
  topic: string;
  status?: string;
  success_rate?: number;
  actions: SuggestionAction[];
}

export interface AdaptiveStudyPlan {
  remedial_topics?: string[];
  challenge_topics?: string[];
  resource_queries?: string[];
  next_quiz_focus?: string[];
  narrative?: string;
  next_step_title?: string | null;
  next_step_why?: string | null;
}

export interface SuggestedNextQuiz {
  num_questions: number;
  difficulty: string;
  focus_topics?: string[];
  rationale?: string;
  short_recap?: string;
}

export interface TopicStudyContent {
  topic: string;
  success_rate_pct?: number;
  weak_under_half?: boolean;
  what_went_wrong?: string;
  why_hint?: string;
  pdf_excerpt?: string;
  topic_summary?: string;
  simple_explanation?: string;
  example_question?: string;
  example_answer_hint?: string;
  micro_task?: string;
  mini_quiz_outline?: string;
}

export interface TopicScoreRow {
  topic: string;
  success_pct: number;
  wrong_count?: number;
  total_attempts?: number;
}

export interface RecommendedMiniQuiz {
  num_questions?: number;
  focus_topics?: string[];
  caption?: string;
}

export interface StudyResourceTip {
  topic?: string;
  short_summary?: string;
  explain_action?: string;
  pdf_section_hint?: string;
  mini_quiz_hint?: string;
  pomodoro_minutes?: number;
}

export interface QuizCoachingPack {
  global_band: string;
  overall_what_wrong?: string;
  overall_why?: string;
  overall_next_steps?: string;
  suggested_quiz: SuggestedNextQuiz;
  topic_contents?: TopicStudyContent[];
  topic_score_rows?: TopicScoreRow[];
  weakest_topic?: string;
  top_error_type?: string;
  top_error_hint?: string;
  natural_feedback_lines?: string[];
  personalized_tip_lines?: string[];
  study_plan_steps?: string[];
  recommended_mini_quiz?: RecommendedMiniQuiz;
  resource_tips?: StudyResourceTip[];
}

/** Mini / zayıf konu quizi — skor ve çeşitlilik için isteğe bağlı alanlar. */
export interface TailoredMiniQuizPayload {
  count: number;
  topics: string[];
  difficulty?: "beginner" | "normal" | "technical";
  /** PDF quiz üretiminde kontrast / farklı soru açıları için (ör. güçlü konulardan seçilen). */
  challengeTopics?: string[];
}

export interface SuggestionResponse {
  very_weak_topics?: string[];
  weak_topics: string[];
  moderate_topics?: string[];
  developing_topics?: string[];
  good_topics?: string[];
  strong_topics?: string[];
  topics: TopicSuggestion[];
  general_actions: SuggestionAction[];
  coach_message?: string | null;
  user_id?: number;
  adaptive_plan?: AdaptiveStudyPlan | null;
  coaching_pack?: QuizCoachingPack | null;
}

export interface TopicProgressItem {
  topic: string;
  previous_success_rate?: number | null;
  current_success_rate: number;
  delta_points?: number | null;
  trend: string;
}

/** GET /quiz/history — one attempt row for the signed-in user. */
export interface QuizHistoryListItemDto {
  id: string;
  attempt_id: number;
  solved_at_iso: string;
  quiz_kind: "standard" | "mini_adaptive" | string;
  quiz_source: "pdf_session" | "text_summary" | "other" | string;
  score: number;
  correct: number;
  wrong: number;
  unanswered: number;
  total_questions: number;
  duration_seconds?: number | null;
  recommended_mini_count?: number;
  suggested_mini_quiz_topic?: string | null;
  focus_topics?: string[];
  recommendation_lines?: string[];
  topic_rows?: {
    topic: string;
    correct: number;
    total: number;
    pct: number;
    status: string;
    wrong: number;
  }[];
  error_summary?: Record<string, number>;
}

export interface QuizHistoryListResponse {
  items: QuizHistoryListItemDto[];
}

/** Persisted quiz summary for Results tab (GET /quiz/user-results). */
export interface QuizResultsPageResponse {
  has_data: boolean;
  attempt_id?: number | null;
  document_id?: string | null;
  total_correct: number;
  total_wrong: number;
  total_unanswered?: number;
  total_question_count?: number;
  total_duration_seconds?: number | null;
  score_percentage: number;
  performance_comment?: string | null;
  recommended_mini_quiz_count?: number;
  /** Latest quiz — primary for coaching and charts */
  topic_analysis?: TopicAnalysisResponse | null;
  lifetime_topic_analysis?: TopicAnalysisResponse | null;
  topic_progress?: TopicProgressItem[];
  question_feedback?: QuizQuestionFeedback[];
  listed_wrong_count?: number;
  wrong_items?: QuizQuestionFeedback[];
  weak_topics?: string[];
  suggested_mini_quiz_topic?: string | null;
  error_type_summary?: Record<string, number>;
  confused_topics_ranked?: string[];
  user_quiz_memory?: UserQuizMemory | null;
  learning_brief?: QuizLearningBrief | null;
  suggestions?: SuggestionResponse | null;
}


export interface PomodoroStartRequest {
  user_id: number;
  topic?: string | null;
  start_time?: string | null;
  planned_duration_seconds?: number | null;
}

export interface PomodoroStartResponse {
  session_id: number;
  start_time: string;
}

export interface PomodoroEndRequest {
  session_id: number;
  end_time?: string | null;
  planned_duration_seconds?: number | null;
  completed_naturally?: boolean;
}

export interface PomodoroEndResponse {
  session_id: number;
  end_time: string;
  duration_seconds: number;
  xp_awarded?: number;
  pomodoro_streak?: number | null;
  message?: string | null;
}

export interface PomodoroStatsResponse {
  user_id: number;
  sessions_count: number;
  total_study_time_seconds: number;
  by_topic_seconds: Record<string, number>;
}

export interface PomodoroSessionSummary {
  id: number;
  topic: string | null;
  duration_seconds: number;
  start_time: string;
  end_time: string | null;
}

export interface StudyStatsProofPomodoroSession {
  scoped_id: string;
  session_id: number;
  topic: string | null;
  duration_seconds: number;
  end_time: string | null;
}

export interface StudyStatsProofQuizAttempt {
  scoped_id: string;
  attempt_id: number;
  total_questions: number;
  total_correct: number;
  total_wrong: number;
  total_unanswered: number;
  score_percentage: number | null;
}

export interface StudyStatsCalculationProof {
  formula_total_focus_time: string;
  formula_avg_attempt_score: string;
  formula_lifetime_accuracy: string;
  pomodoro_completed_sessions_count: number;
  pomodoro_total_seconds_from_sessions: number;
  pomodoro_total_seconds_from_topic_rollup: number;
  pomodoro_sample_limit: number;
  pomodoro_sample_truncated: boolean;
  pomodoro_sample_sessions: StudyStatsProofPomodoroSession[];
  quiz_attempts_count: number;
  quiz_correct_numerator: number;
  quiz_accuracy_denominator: number;
  quiz_lifetime_accuracy_percent: number | null;
  quiz_avg_score_sum_percent: number;
  quiz_avg_score_attempt_count: number;
  quiz_avg_score_percent: number | null;
  quiz_attempt_sample_limit: number;
  quiz_attempt_sample_truncated: boolean;
  quiz_attempt_sample: StudyStatsProofQuizAttempt[];
}

/** GET /stats/summary — pomodoro time + quiz rollups. */
export interface StudyStats {
  user_id: number;
  total_study_time_seconds: number;
  pomodoro_sessions_count: number;
  total_quiz_count: number;
  average_quiz_score: number | null;
  weak_topics: string[];
  strong_topics: string[];
  /** Sum of correct / wrong / blank across all stored quiz attempts. */
  quiz_sum_correct: number;
  quiz_sum_wrong: number;
  quiz_sum_unanswered: number;
  quiz_sum_question_slots: number;
  /** 100 × quiz_sum_correct / quiz_sum_question_slots (every question weighted equally). */
  quiz_lifetime_accuracy_percent: number | null;
  pomodoro_by_topic_seconds: Record<string, number>;
  pomodoro_recent_sessions: PomodoroSessionSummary[];
  calculation_proof: StudyStatsCalculationProof | null;
}

export interface ProfileBadgeItem {
  slug: string;
  title: string;
  description: string;
  earned: boolean;
}

export interface ProfileResponse {
  xp: number;
  locale: string;
  badges: ProfileBadgeItem[];
  pomodoro_streak?: number;
}

/** GET /auth/me, PATCH /auth/me, POST password/photo */
export interface UserMeResponse {
  user_id: number;
  email: string;
  username: string | null;
  biography: string | null;
  profile_photo_url: string | null;
}

export interface PdfAssistantUploadResponse {
  document_id?: string;
  brief_summary?: string;
  summary?: string;
  summary_en?: string;
  summary_tr?: string;
  filename?: string;
  quiz?: { questions: QuizQuestion[]; total_questions: number };
  error?: string;
}

export interface PdfChatResponseBody {
  reply: string;
}

/** GET /pdf/session-history — one row per PDF chat thread for the signed-in user. */
export interface PdfSessionHistoryMessageDto {
  role: "user" | "assistant";
  content: string;
}

export interface PdfSessionHistoryItemDto {
  key: string;
  document_id: string;
  filename: string;
  messages: PdfSessionHistoryMessageDto[];
  updated_at?: string;
}

export interface PdfSessionHistoryResponse {
  items: PdfSessionHistoryItemDto[];
}

/** Quick Summary / apply-mode pipeline quality (optional on API). */
export type PdfSummaryQuality = "good" | "partial" | "weak";

export interface PdfApplyModeResponseBody {
  reply: string;
  summary?: string;
  mode?: string;
  level?: string;
  history_id?: number;
  pipeline_duration_ms?: number | null;
  pipeline_llm_calls?: number | null;
  pipeline_used_map_reduce?: boolean | null;
  summary_quality?: PdfSummaryQuality | null;
  summary_warning?: string | null;
  used_fallback?: boolean | null;
  fallback_reason?: string | null;
  summary_quality_warning?: string | null;
  summary_generation_note?: string | null;
}

export type SummaryStyle = "concise" | "balanced" | "detailed";
export type SummaryFormat = "mixed" | "bullets" | "prose";
export type SummaryLength = "short" | "medium" | "long";

/** POST /pdf/generate-summary — optional pipeline / UX fields (backend may omit). */
export interface PdfDynamicSummaryResponseBody {
  summary: string;
  mode: string;
  explain_level: string;
  summary_style: SummaryStyle;
  summary_format: SummaryFormat;
  summary_length: SummaryLength;
  history_id?: number | null;
  /** Coarse quality from backend heuristics + fallback path. */
  summary_quality?: PdfSummaryQuality | null;
  /** User-facing warning (quality, OCR, partial fallback). */
  summary_warning?: string | null;
  used_fallback?: boolean | null;
  /** Pipe-separated tags, e.g. `extractive|partial_non_llm`. */
  fallback_reason?: string | null;
  pipeline_duration_ms?: number | null;
  pipeline_llm_calls?: number | null;
  pipeline_used_map_reduce?: boolean | null;
  pipeline_map_chunks_in?: number | null;
  pipeline_map_chunks_after_quality?: number | null;
  pipeline_map_chunks_skipped_quality?: number | null;
  pipeline_map_chunks_out?: number | null;
  pipeline_map_phase_ms?: number | null;
  pipeline_cache_hit?: boolean | null;
  pipeline_fallback_path?: string | null;
  /** Legacy mirror of `summary_warning`. */
  summary_quality_warning?: string | null;
  summary_generation_note?: string | null;
  quiz?: {
    quiz_kind?: string;
    difficulty?: string;
    questions?: QuizQuestion[];
    total_questions?: number;
  } | null;
}

export type StudyChatMode = "tutor_chat" | "quick_summary" | "explain_simple" | "exam_focus" | "quiz_coach";

export type ExplainLevel = "beginner" | "normal" | "technical";

export type StudyOutcomeKind = "exam_focus" | "quick_summary" | "explain_overview";

export interface StudyOutcomeResponse {
  outcome: string;
  content: string;
}

/** POST /pdf/document-status */
export interface PdfDocumentStatusResponse {
  ok: boolean;
  error?: string | null;
  message?: string | null;
}

export interface BestResourcesResponse {
  topic: string;
  video_url: string;
  web_url: string;
}

/** GET /study/academic-plan/latest — deadline study planner. */
export type AcademicPlanTaskKind = "study" | "review" | "quiz" | "buffer";
export type AcademicPlanTaskStatus = "pending" | "completed" | "missed";

export interface AcademicPlanTask {
  task_id: string;
  date_iso: string;
  title: string;
  kind: AcademicPlanTaskKind;
  minutes_estimate: number;
  status: AcademicPlanTaskStatus;
  topic_focus?: string | null;
}

export interface AcademicPlanView {
  plan_id: number;
  course_name: string;
  goal_text: string;
  deadline_date: string;
  daily_hours: number;
  study_days: number[];
  weak_topics: string[];
  confident_topics: string[];
  topic_outline: string[];
  days_until_deadline: number;
  study_slots_remaining: number;
  tasks: AcademicPlanTask[];
  priority_high: string[];
  priority_medium: string[];
  priority_strong: string[];
  today_iso: string;
  today_recommendation: AcademicPlanTask | null;
  is_finished: boolean;
  finished_at?: string | null;
  completed_count: number;
  missed_count: number;
  total_task_count: number;
}

export interface AcademicPlanLatestResponse {
  has_plan: boolean;
  plan: AcademicPlanView | null;
}

export interface AcademicPlanGenerateRequest {
  course_name: string;
  goal_text: string;
  deadline_date: string;
  daily_hours: number;
  study_days: number[];
  weak_topics: string[];
  confident_topics: string[];
  topic_outline: string[];
  locale: "en" | "tr";
}

export interface AcademicPlanHistoryItem {
  plan_id: number;
  course_name: string;
  goal_text: string;
  finished_at: string;
  completed_count: number;
  missed_count: number;
  total_task_count: number;
}

export interface AcademicPlanHistoryResponse {
  items: AcademicPlanHistoryItem[];
}

