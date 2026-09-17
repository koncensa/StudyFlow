import {
  QuizLearningBrief,
  QuizQuestionFeedback,
  QuizResultsPageResponse,
  QuizSubmissionResponse,
  TopicAnalysisResponse,
  TopicPerformance,
  TopicSnapshotLine,
} from "../models/types";
import { quizScorePercentFromAttempt } from "./quiz-score-display";

/** JSON / proxy quirks: string "false" must not count as truthy "wrong". */
export function coerceIsCorrect(raw: unknown): boolean {
  if (raw === undefined || raw === null) {
    return true;
  }
  if (raw === true || raw === 1) {
    return true;
  }
  if (raw === false || raw === 0) {
    return false;
  }
  if (typeof raw === "string") {
    const s = raw.trim().toLowerCase();
    if (s === "true" || s === "1") {
      return true;
    }
    if (s === "false" || s === "0") {
      return false;
    }
  }
  return true;
}

export function feedbackRowIsCorrect(f: QuizQuestionFeedback): boolean {
  const x = f as unknown as { is_correct?: unknown; isCorrect?: unknown };
  const raw = x.is_correct !== undefined && x.is_correct !== null ? x.is_correct : x.isCorrect;
  return coerceIsCorrect(raw);
}

export function feedbackRowIsUnanswered(f: QuizQuestionFeedback): boolean {
  const x = f as unknown as { is_unanswered?: unknown; isUnanswered?: unknown };
  const raw = x.is_unanswered !== undefined && x.is_unanswered !== null ? x.is_unanswered : x.isUnanswered;
  if (raw === true || raw === 1) {
    return true;
  }
  if (typeof raw === "string" && raw.trim().toLowerCase() === "true") {
    return true;
  }
  if (f.error_type === "unanswered") {
    return true;
  }
  return false;
}

export function normalizeQuestionFeedbackItem(raw: unknown): QuizQuestionFeedback | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const x = raw as Record<string, unknown>;
  const icRaw = x.is_correct ?? x.isCorrect;
  const iuRaw = x.is_unanswered ?? x.isUnanswered;
  const isUn =
    iuRaw === true ||
    iuRaw === 1 ||
    (typeof iuRaw === "string" && iuRaw.trim().toLowerCase() === "true") ||
    String(x.error_type ?? x.errorType ?? "").trim().toLowerCase() === "unanswered";
  return {
    question_index: Math.max(0, Math.round(Number(x.question_index ?? x.questionIndex ?? 0))),
    topic: String(x.topic ?? "").trim() || "General",
    question_text:
      x.question_text != null && String(x.question_text).trim()
        ? String(x.question_text)
        : x.questionText != null && String(x.questionText).trim()
        ? String(x.questionText)
        : null,
    is_correct: coerceIsCorrect(icRaw),
    is_unanswered: isUn,
    selected_answer: String(x.selected_answer ?? x.selectedAnswer ?? ""),
    user_answer: x.user_answer != null ? String(x.user_answer) : undefined,
    correct_answer: String(x.correct_answer ?? x.correctAnswer ?? ""),
    question_type:
      x.question_type != null && String(x.question_type).trim()
        ? String(x.question_type)
        : x.questionType != null && String(x.questionType).trim()
        ? String(x.questionType)
        : null,
    error_type:
      x.error_type != null && String(x.error_type).trim()
        ? String(x.error_type)
        : x.errorType != null && String(x.errorType).trim()
        ? String(x.errorType)
        : null,
    confused_concepts: Array.isArray(x.confused_concepts)
      ? (x.confused_concepts as unknown[]).map((c) => String(c ?? "").trim()).filter(Boolean)
      : Array.isArray(x.confusedConcepts)
      ? (x.confusedConcepts as unknown[]).map((c) => String(c ?? "").trim()).filter(Boolean)
      : [],
    why_wrong:
      x.why_wrong != null && String(x.why_wrong).trim()
        ? String(x.why_wrong)
        : x.whyWrong != null && String(x.whyWrong).trim()
        ? String(x.whyWrong)
        : null,
    teaching_snippet:
      x.teaching_snippet != null && String(x.teaching_snippet).trim()
        ? String(x.teaching_snippet)
        : x.teachingSnippet != null && String(x.teachingSnippet).trim()
        ? String(x.teachingSnippet)
        : null,
    hint: x.hint != null && String(x.hint).trim() ? String(x.hint) : null,
  };
}

export function normalizeQuestionFeedbackList(arr: unknown): QuizQuestionFeedback[] {
  if (!Array.isArray(arr)) {
    return [];
  }
  return arr.map((row) => normalizeQuestionFeedbackItem(row)).filter((x): x is QuizQuestionFeedback => x != null);
}

/**
 * When API totals disagree with per-question feedback (e.g. legacy rows that counted blanks as wrong),
 * prefer feedback-derived counts for a full attempt (feedback length matches declared total).
 */
export function reconcileQuizAttemptTotals(args: {
  total_correct: number;
  total_wrong: number;
  total_unanswered: number;
  total_question_count: number;
  question_feedback: QuizQuestionFeedback[];
}): {
  total_correct: number;
  total_wrong: number;
  total_unanswered: number;
  total_question_count: number;
} {
  const { question_feedback: fb } = args;
  let { total_correct, total_wrong, total_unanswered, total_question_count } = args;
  const n = fb.length;
  if (!n) {
    return { total_correct, total_wrong, total_unanswered, total_question_count };
  }
  const tuF = fb.filter((f) => feedbackRowIsUnanswered(f)).length;
  const tcF = fb.filter((f) => feedbackRowIsCorrect(f)).length;
  const twF = Math.max(0, n - tcF - tuF);
  const tqKnown = total_question_count > 0 ? total_question_count : n;
  if (tqKnown !== n) {
    if (total_question_count === 0) {
      return { total_correct, total_wrong, total_unanswered, total_question_count: n };
    }
    return { total_correct, total_wrong, total_unanswered, total_question_count };
  }
  if (tcF !== total_correct || twF !== total_wrong || tuF !== total_unanswered) {
    return {
      total_correct: tcF,
      total_wrong: twF,
      total_unanswered: tuF,
      total_question_count: tqKnown,
    };
  }
  if (total_question_count === 0) {
    return { total_correct, total_wrong, total_unanswered, total_question_count: n };
  }
  return { total_correct, total_wrong, total_unanswered, total_question_count };
}

function coerceSnapshotLine(raw: unknown): TopicSnapshotLine | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const x = raw as Record<string, unknown>;
  return {
    topic: String(x.topic ?? ""),
    correct_count: Number(x.correct_count ?? 0),
    total_attempts: Number(x.total_attempts ?? 0),
    success_rate: Number(x.success_rate ?? 0),
    band_label: String(x.band_label ?? ""),
    coaching_line: String(x.coaching_line ?? ""),
  };
}

export function coerceLearningBrief(raw: unknown): QuizLearningBrief | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const o = raw as Record<string, unknown>;
  const lines = (arr: unknown) =>
    Array.isArray(arr) ? arr.map(coerceSnapshotLine).filter((x): x is TopicSnapshotLine => x != null) : [];
  const weak = lines(o.weak_topics);
  const dev = lines(o.developing_topics);
  const strong = lines(o.strong_topics);
  if (!String(o.headline ?? "").trim() && !weak.length && !dev.length && !strong.length) {
    return null;
  }
  return {
    headline: String(o.headline ?? ""),
    weak_topics: weak,
    developing_topics: dev,
    strong_topics: strong,
    what_to_do_next: Array.isArray(o.what_to_do_next)
      ? (o.what_to_do_next as unknown[]).map((s) => String(s ?? "").trim()).filter(Boolean)
      : [],
    mini_quiz_hint: String(o.mini_quiz_hint ?? ""),
    resource_hints: Array.isArray(o.resource_hints)
      ? (o.resource_hints as unknown[]).map((s) => String(s ?? "").trim()).filter(Boolean)
      : [],
  };
}

export function coerceTopicAnalysis(taRaw: unknown): TopicAnalysisResponse {
  if (!taRaw || typeof taRaw !== "object") {
    return {
      topics: [],
      topics_under_half: [],
      very_weak_topics: [],
      weak_topics: [],
      developing_topics: [],
      good_topics: [],
      strong_topics: [],
      moderate_topics: [],
    };
  }
  const t = taRaw as Record<string, unknown>;
  const rawTopics = Array.isArray(t.topics) ? t.topics : [];
  const topics = rawTopics.map((row) => {
    const r = row as Record<string, unknown>;
    const sr = Number(r.success_rate ?? 0);
    const iwu = r.is_weak_under_half;
    return {
      topic: String(r.topic ?? ""),
      correct_count: Number(r.correct_count ?? 0),
      wrong_count: Number(r.wrong_count ?? 0),
      total_attempts: Number(r.total_attempts ?? 0),
      success_rate: sr,
      status: String(r.status ?? ""),
      is_weak_under_half:
        typeof iwu === "boolean" ? iwu : sr < 0.5,
    } as TopicPerformance;
  });
  return {
    topics,
    topics_under_half: Array.isArray(t.topics_under_half) ? (t.topics_under_half as string[]) : [],
    very_weak_topics: Array.isArray(t.very_weak_topics) ? (t.very_weak_topics as string[]) : [],
    weak_topics: Array.isArray(t.weak_topics) ? (t.weak_topics as string[]) : [],
    developing_topics: Array.isArray(t.developing_topics) ? (t.developing_topics as string[]) : [],
    good_topics: Array.isArray(t.good_topics) ? (t.good_topics as string[]) : [],
    strong_topics: Array.isArray(t.strong_topics) ? (t.strong_topics as string[]) : [],
    moderate_topics: Array.isArray(t.moderate_topics) ? (t.moderate_topics as string[]) : [],
  };
}

/**
 * Ensures topic_analysis + topics[] exist (API shape / camelCase quirks).
 */
export function normalizeQuizSubmissionResponse(raw: unknown): QuizSubmissionResponse | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const r = raw as Record<string, unknown>;
  const taIn = r.topic_analysis ?? r.topicAnalysis;
  const topic_analysis = coerceTopicAnalysis(taIn);
  const question_feedback = normalizeQuestionFeedbackList(r.question_feedback ?? (r as { questionFeedback?: unknown }).questionFeedback);
  const wrong_items = normalizeQuestionFeedbackList(r.wrong_items ?? (r as { wrongItems?: unknown }).wrongItems);
  let total_correct = Number(r.total_correct ?? 0);
  let total_wrong = Number(r.total_wrong ?? 0);
  let total_unanswered = (() => {
    const n = Number(r.total_unanswered ?? (r as { totalUnanswered?: unknown }).totalUnanswered);
    return Number.isFinite(n) && n >= 0 ? Math.round(n) : 0;
  })();
  let total_question_count = (() => {
    const n = Number(r.total_question_count ?? (r as { totalQuestionCount?: unknown }).totalQuestionCount);
    return Number.isFinite(n) && n >= 0 ? Math.round(n) : 0;
  })();
  const rec = reconcileQuizAttemptTotals({
    total_correct,
    total_wrong,
    total_unanswered,
    total_question_count,
    question_feedback,
  });
  total_correct = rec.total_correct;
  total_wrong = rec.total_wrong;
  total_unanswered = rec.total_unanswered;
  total_question_count = rec.total_question_count;

  const listedRaw = r.listed_wrong_count ?? (r as { listedWrongCount?: unknown }).listedWrongCount;
  const listedNum = Number(listedRaw);
  const wrongFromFeedback = question_feedback.filter((f) => !feedbackRowIsCorrect(f) && !feedbackRowIsUnanswered(f)).length;
  const listed_wrong_count =
    Number.isFinite(listedNum) && listedNum >= 0
      ? Math.round(listedNum)
      : wrong_items.length || wrongFromFeedback;
  const apiScore = Number(r.score_percentage ?? 0);
  const score_percentage = quizScorePercentFromAttempt({
    total_correct,
    total_wrong,
    total_unanswered,
    total_question_count,
    question_feedback,
    score_percentage: apiScore,
  });

  return {
    attempt_id: (r.attempt_id as number | null | undefined) ?? null,
    total_correct,
    total_wrong,
    total_unanswered,
    total_question_count,
    total_duration_seconds: (() => {
      const raw = r.total_duration_seconds ?? (r as { totalDurationSeconds?: unknown }).totalDurationSeconds;
      const n = Number(raw);
      return Number.isFinite(n) && n >= 0 ? Math.round(n) : null;
    })(),
    quiz_source: (() => {
      const v = r.quiz_source ?? (r as { quizSource?: unknown }).quizSource;
      const s = v != null ? String(v).trim() : "";
      return s.length ? s : null;
    })(),
    score_percentage,
    performance_comment:
      r.performance_comment != null && String(r.performance_comment).trim()
        ? String(r.performance_comment).trim()
        : null,
    recommended_mini_quiz_count: (() => {
      const raw = r.recommended_mini_quiz_count ?? (r as { recommendedMiniQuizCount?: unknown }).recommendedMiniQuizCount;
      const n = Number(raw);
      return Number.isFinite(n) && n >= 1 ? Math.min(10, Math.round(n)) : undefined;
    })(),
    topic_analysis,
    question_feedback,
    listed_wrong_count,
    wrong_items,
    weak_topics: Array.isArray(r.weak_topics)
      ? (r.weak_topics as unknown[]).map((s) => String(s ?? "").trim()).filter(Boolean)
      : Array.isArray((r as { weakTopics?: unknown }).weakTopics)
      ? ((r as { weakTopics?: unknown[] }).weakTopics as unknown[]).map((s) => String(s ?? "").trim()).filter(Boolean)
      : [],
    suggested_mini_quiz_topic: (() => {
      const v = r.suggested_mini_quiz_topic ?? (r as { suggestedMiniQuizTopic?: unknown }).suggestedMiniQuizTopic;
      const s = v != null ? String(v).trim() : "";
      return s.length ? s : null;
    })(),
    error_type_summary:
      r.error_type_summary && typeof r.error_type_summary === "object"
        ? (r.error_type_summary as Record<string, number>)
        : {},
    confused_topics_ranked: Array.isArray(r.confused_topics_ranked) ? (r.confused_topics_ranked as string[]) : [],
    follow_up_actions: Array.isArray(r.follow_up_actions) ? (r.follow_up_actions as string[]) : [],
    user_quiz_memory: (r.user_quiz_memory as QuizSubmissionResponse["user_quiz_memory"]) ?? null,
    learning_brief: coerceLearningBrief(r.learning_brief ?? (r as { learningBrief?: unknown }).learningBrief),
  };
}

function coerceTopicProgress(raw: unknown): QuizResultsPageResponse["topic_progress"] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw as QuizResultsPageResponse["topic_progress"];
}

/** Normalizes GET /quiz/user-results (snake_case / camelCase, topic lists). */
export function normalizeQuizResultsPageResponse(raw: unknown): QuizResultsPageResponse | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const r = raw as Record<string, unknown>;
  const has_data = Boolean(r.has_data);
  const taIn = r.topic_analysis ?? r.topicAnalysis;
  const ltIn = r.lifetime_topic_analysis ?? r.lifetimeTopicAnalysis;
  const topic_analysis =
    taIn != null && typeof taIn === "object" ? coerceTopicAnalysis(taIn) : has_data ? coerceTopicAnalysis(null) : null;
  const lifetime_topic_analysis =
    ltIn != null && typeof ltIn === "object" ? coerceTopicAnalysis(ltIn) : undefined;

  const question_feedback = normalizeQuestionFeedbackList(r.question_feedback ?? (r as { questionFeedback?: unknown }).questionFeedback);
  let total_correct = Number(r.total_correct ?? 0);
  let total_wrong = Number(r.total_wrong ?? 0);
  let total_unanswered = (() => {
    const n = Number(r.total_unanswered ?? (r as { totalUnanswered?: unknown }).totalUnanswered);
    return Number.isFinite(n) && n >= 0 ? Math.round(n) : 0;
  })();
  let total_question_count = (() => {
    const n = Number(r.total_question_count ?? (r as { totalQuestionCount?: unknown }).totalQuestionCount);
    return Number.isFinite(n) && n >= 0 ? Math.round(n) : 0;
  })();
  const rec = reconcileQuizAttemptTotals({
    total_correct,
    total_wrong,
    total_unanswered,
    total_question_count,
    question_feedback,
  });
  total_correct = rec.total_correct;
  total_wrong = rec.total_wrong;
  total_unanswered = rec.total_unanswered;
  total_question_count = rec.total_question_count;
  const apiScorePersisted = Number(r.score_percentage ?? 0);
  const score_percentage = quizScorePercentFromAttempt({
    total_correct,
    total_wrong,
    total_unanswered,
    total_question_count,
    question_feedback,
    score_percentage: apiScorePersisted,
  });

  return {
    has_data,
    attempt_id: (r.attempt_id as number | null | undefined) ?? null,
    document_id: (r.document_id as string | null | undefined) ?? null,
    total_correct,
    total_wrong,
    total_unanswered,
    total_question_count,
    total_duration_seconds: (() => {
      const raw = r.total_duration_seconds ?? (r as { totalDurationSeconds?: unknown }).totalDurationSeconds;
      const n = Number(raw);
      return Number.isFinite(n) && n >= 0 ? Math.round(n) : null;
    })(),
    score_percentage,
    performance_comment:
      r.performance_comment != null && String(r.performance_comment).trim()
        ? String(r.performance_comment).trim()
        : null,
    recommended_mini_quiz_count: (() => {
      const raw = r.recommended_mini_quiz_count ?? (r as { recommendedMiniQuizCount?: unknown }).recommendedMiniQuizCount;
      const n = Number(raw);
      return Number.isFinite(n) && n >= 1 ? Math.min(10, Math.round(n)) : undefined;
    })(),
    topic_analysis: topic_analysis ?? null,
    lifetime_topic_analysis: lifetime_topic_analysis ?? null,
    topic_progress: coerceTopicProgress(r.topic_progress ?? r.topicProgress),
    question_feedback,
    listed_wrong_count: (() => {
      const listedRaw = r.listed_wrong_count ?? (r as { listedWrongCount?: unknown }).listedWrongCount;
      const n = Number(listedRaw);
      return Number.isFinite(n) && n >= 0 ? Math.round(n) : undefined;
    })(),
    wrong_items: normalizeQuestionFeedbackList(r.wrong_items ?? (r as { wrongItems?: unknown }).wrongItems),
    weak_topics: Array.isArray(r.weak_topics)
      ? (r.weak_topics as unknown[]).map((s) => String(s ?? "").trim()).filter(Boolean)
      : Array.isArray((r as { weakTopics?: unknown }).weakTopics)
      ? ((r as { weakTopics?: unknown[] }).weakTopics as unknown[]).map((s) => String(s ?? "").trim()).filter(Boolean)
      : [],
    suggested_mini_quiz_topic: (() => {
      const v = r.suggested_mini_quiz_topic ?? (r as { suggestedMiniQuizTopic?: unknown }).suggestedMiniQuizTopic;
      const s = v != null ? String(v).trim() : "";
      return s.length ? s : null;
    })(),
    error_type_summary:
      r.error_type_summary && typeof r.error_type_summary === "object"
        ? (r.error_type_summary as Record<string, number>)
        : {},
    confused_topics_ranked: Array.isArray(r.confused_topics_ranked)
      ? (r.confused_topics_ranked as string[])
      : [],
    user_quiz_memory: (r.user_quiz_memory as QuizResultsPageResponse["user_quiz_memory"]) ?? null,
    learning_brief: coerceLearningBrief(r.learning_brief ?? (r as { learningBrief?: unknown }).learningBrief),
    suggestions: (r.suggestions as QuizResultsPageResponse["suggestions"]) ?? null,
  };
}
