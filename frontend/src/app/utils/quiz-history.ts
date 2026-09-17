import { QuizHistoryListItemDto, QuizSubmissionResponse, SuggestionResponse } from "../models/types";

export type QuizKind = "standard" | "mini_adaptive";

/** Per-account local cache key (user id + email — same pattern as PDF session history). */
export function buildQuizHistoryStorageKey(
  userId: number | null | undefined,
  email: string | null | undefined
): string | null {
  const uid = Number(userId);
  if (!Number.isFinite(uid) || uid < 1) {
    return null;
  }
  const em = (email || "").trim().toLowerCase();
  if (!em) {
    return null;
  }
  return `studyflow:quiz-history:v2:${uid}_${encodeURIComponent(em).slice(0, 120)}`;
}

/** Drop legacy v1 keys (user id only — unsafe when DB ids are reused). */
export function clearLegacyQuizHistoryStorage(userId: number): void {
  try {
    localStorage.removeItem(`studyflow:quiz-history:v1:${userId}`);
  } catch {
    // Best effort only.
  }
}

export function mapServerQuizHistoryItems(items: QuizHistoryListItemDto[]): QuizHistoryEntry[] {
  if (!Array.isArray(items)) {
    return [];
  }
  const out: QuizHistoryEntry[] = [];
  for (const row of items) {
    if (!row) {
      continue;
    }
    const kind: QuizKind = row.quiz_kind === "mini_adaptive" ? "mini_adaptive" : "standard";
    const srcRaw = String(row.quiz_source ?? "").trim().toLowerCase();
    const quizSource: QuizHistoryEntry["quizSource"] =
      srcRaw === "pdf_session" || srcRaw === "text_summary" ? srcRaw : "other";
    const totalQuestions = Math.max(1, Math.round(Number(row.total_questions ?? 0) || 0));
    out.push({
      id: String(row.id ?? "").trim() || `db-${row.attempt_id}`,
      solvedAtIso: String(row.solved_at_iso ?? ""),
      quizKind: kind,
      quizSource,
      score: Number(row.score ?? 0),
      correct: Math.max(0, Math.round(Number(row.correct ?? 0))),
      wrong: Math.max(0, Math.round(Number(row.wrong ?? 0))),
      unanswered: Math.max(0, Math.round(Number(row.unanswered ?? 0))),
      totalQuestions,
      durationSeconds:
        row.duration_seconds == null || Number.isNaN(Number(row.duration_seconds))
          ? null
          : Math.max(0, Math.round(Number(row.duration_seconds))),
      recommendedMiniCount: Math.max(1, Math.min(15, Math.round(Number(row.recommended_mini_count ?? 3) || 3))),
      suggestedMiniQuizTopic:
        row.suggested_mini_quiz_topic != null && String(row.suggested_mini_quiz_topic).trim()
          ? String(row.suggested_mini_quiz_topic).trim()
          : null,
      focusTopics: Array.isArray(row.focus_topics)
        ? row.focus_topics.map((x) => String(x ?? "").trim()).filter(Boolean).slice(0, 8)
        : [],
      recommendationLines: Array.isArray(row.recommendation_lines)
        ? row.recommendation_lines.map((x) => String(x ?? "").trim()).filter(Boolean).slice(0, 8)
        : [],
      topicRows: Array.isArray(row.topic_rows)
        ? row.topic_rows
            .map((x) => ({
              topic: String(x?.topic ?? "").trim(),
              correct: Math.max(0, Math.round(Number(x?.correct ?? 0))),
              total: Math.max(0, Math.round(Number(x?.total ?? 0))),
              pct: Math.max(0, Math.min(100, Math.round(Number(x?.pct ?? 0)))),
              status: String(x?.status ?? "").trim() || "unknown",
              wrong: Math.max(0, Math.round(Number(x?.wrong ?? 0))),
            }))
            .filter((x) => x.topic.length > 0)
            .slice(0, 24)
        : [],
      errorSummary:
        row.error_summary && typeof row.error_summary === "object"
          ? Object.keys(row.error_summary).reduce<Record<string, number>>((acc, k) => {
              const n = Math.max(0, Math.round(Number((row.error_summary as Record<string, number>)[k] ?? 0)));
              if (n > 0) {
                acc[k] = n;
              }
              return acc;
            }, {})
          : {},
    });
  }
  return out;
}

export interface QuizHistoryEntry {
  id: string;
  solvedAtIso: string;
  quizKind: QuizKind;
  quizSource: "pdf_session" | "text_summary" | "other";
  score: number;
  correct: number;
  wrong: number;
  unanswered: number;
  totalQuestions: number;
  durationSeconds: number | null;
  recommendedMiniCount: number;
  suggestedMiniQuizTopic: string | null;
  focusTopics: string[];
  recommendationLines: string[];
  topicRows?: {
    topic: string;
    correct: number;
    total: number;
    pct: number;
    status: string;
    wrong: number;
  }[];
  errorSummary?: Record<string, number>;
}

export function loadQuizHistoryFromStorage(
  storageKey: string | null,
  historyLimit: number,
  maxMiniQuizQuestions: number
): QuizHistoryEntry[] {
  if (!storageKey) {
    return [];
  }
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    const list: QuizHistoryEntry[] = [];
    for (const item of parsed) {
      if (!item || typeof item !== "object") {
        continue;
      }
      const row = item as Partial<QuizHistoryEntry>;
      const kind: QuizKind = row.quizKind === "mini_adaptive" ? "mini_adaptive" : "standard";
      const srcRaw = String(row.quizSource ?? "").trim().toLowerCase();
      const quizSource: QuizHistoryEntry["quizSource"] =
        srcRaw === "pdf_session" || srcRaw === "text_summary" ? srcRaw : "other";
      const totalRaw = Number(row.totalQuestions ?? 0);
      const totalQuestions = Number.isFinite(totalRaw) && totalRaw > 0 ? Math.round(totalRaw) : 0;
      if (!totalQuestions) {
        continue;
      }
      list.push({
        id: String(row.id ?? "").trim() || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        solvedAtIso: String(row.solvedAtIso ?? ""),
        quizKind: kind,
        quizSource,
        score: Number(row.score ?? 0),
        correct: Math.max(0, Math.round(Number(row.correct ?? 0))),
        wrong: Math.max(0, Math.round(Number(row.wrong ?? 0))),
        unanswered: Math.max(0, Math.round(Number(row.unanswered ?? 0))),
        totalQuestions,
        durationSeconds:
          row.durationSeconds == null || Number.isNaN(Number(row.durationSeconds))
            ? null
            : Math.max(0, Math.round(Number(row.durationSeconds))),
        recommendedMiniCount: Math.max(
          1,
          Math.min(maxMiniQuizQuestions, Math.round(Number(row.recommendedMiniCount ?? 3) || 3))
        ),
        suggestedMiniQuizTopic:
          row.suggestedMiniQuizTopic != null && String(row.suggestedMiniQuizTopic).trim()
            ? String(row.suggestedMiniQuizTopic).trim()
            : null,
        focusTopics: Array.isArray(row.focusTopics)
          ? row.focusTopics.map((x) => String(x ?? "").trim()).filter(Boolean).slice(0, 8)
          : [],
        recommendationLines: Array.isArray(row.recommendationLines)
          ? row.recommendationLines.map((x) => String(x ?? "").trim()).filter(Boolean).slice(0, 8)
          : [],
        topicRows: Array.isArray(row.topicRows)
          ? row.topicRows
              .map((x) => ({
                topic: String((x as any)?.topic ?? "").trim(),
                correct: Math.max(0, Math.round(Number((x as any)?.correct ?? 0))),
                total: Math.max(0, Math.round(Number((x as any)?.total ?? 0))),
                pct: Math.max(0, Math.min(100, Math.round(Number((x as any)?.pct ?? 0)))),
                status: String((x as any)?.status ?? "").trim() || "unknown",
                wrong: Math.max(0, Math.round(Number((x as any)?.wrong ?? 0))),
              }))
              .filter((x) => x.topic.length > 0)
              .slice(0, 24)
          : [],
        errorSummary:
          row.errorSummary && typeof row.errorSummary === "object"
            ? Object.keys(row.errorSummary).reduce<Record<string, number>>((acc, k) => {
                const n = Math.max(0, Math.round(Number((row.errorSummary as any)[k] ?? 0)));
                if (n > 0) {
                  acc[k] = n;
                }
                return acc;
              }, {})
            : {},
      });
    }
    return list.slice(0, historyLimit);
  } catch {
    return [];
  }
}

export function saveQuizHistoryToStorage(
  storageKey: string | null,
  history: QuizHistoryEntry[],
  historyLimit: number
): void {
  if (!storageKey) {
    return;
  }
  try {
    localStorage.setItem(storageKey, JSON.stringify(history.slice(0, historyLimit)));
  } catch {
    // Best effort only.
  }
}

export function clearQuizHistoryInStorage(storageKey: string | null): void {
  if (!storageKey) {
    return;
  }
  try {
    localStorage.removeItem(storageKey);
  } catch {
    // Best effort only.
  }
}

function inferQuizKindForHistory(activeQuizKind: QuizKind | null, submission: QuizSubmissionResponse): QuizKind {
  if (activeQuizKind) {
    return activeQuizKind;
  }
  const total = Math.max(0, Math.round(Number(submission.total_question_count ?? 0)));
  if (total > 0 && total <= 6) {
    return "mini_adaptive";
  }
  return "standard";
}

function buildHistoryRecommendations(
  submission: QuizSubmissionResponse,
  suggestions: SuggestionResponse | null
): string[] {
  const lines: string[] = [];
  const push = (value: string | null | undefined) => {
    const text = String(value ?? "").trim();
    if (!text) {
      return;
    }
    if (lines.some((x) => x.toLowerCase() === text.toLowerCase())) {
      return;
    }
    lines.push(text);
  };
  push(submission.performance_comment);
  for (const item of submission.follow_up_actions || []) {
    push(item);
  }
  const brief = submission.learning_brief;
  if (brief?.what_to_do_next?.length) {
    for (const item of brief.what_to_do_next) {
      push(item);
    }
  }
  const s = suggestions;
  if (s?.coach_message) {
    push(s.coach_message);
  }
  if (s?.coaching_pack?.study_plan_steps?.length) {
    for (const item of s.coaching_pack.study_plan_steps) {
      push(item);
    }
  }
  if (s?.general_actions?.length) {
    for (const action of s.general_actions.slice(0, 4)) {
      push(action.message);
    }
  }
  return lines.slice(0, 8);
}

function buildHistoryFocusTopics(
  submission: QuizSubmissionResponse,
  suggestions: SuggestionResponse | null
): string[] {
  const out: string[] = [];
  const add = (value: string | null | undefined) => {
    const text = String(value ?? "").trim();
    if (!text) {
      return;
    }
    if (!out.some((x) => x.toLowerCase() === text.toLowerCase())) {
      out.push(text);
    }
  };
  add(submission.suggested_mini_quiz_topic);
  for (const topic of submission.weak_topics || []) {
    add(topic);
  }
  const focus = suggestions?.coaching_pack?.recommended_mini_quiz?.focus_topics || [];
  for (const topic of focus) {
    add(topic);
  }
  return out.slice(0, 8);
}

export function buildQuizHistoryEntry(
  submission: QuizSubmissionResponse,
  suggestions: SuggestionResponse | null,
  params: {
    activeQuizKind: QuizKind | null;
    quizSource: "pdf_session" | "text_summary";
    quizQuestionsLength: number;
    maxMiniQuizQuestions: number;
  }
): QuizHistoryEntry {
  const kind = inferQuizKindForHistory(params.activeQuizKind, submission);
  const sourceRaw = String(submission.quiz_source ?? params.quizSource ?? "").trim().toLowerCase();
  const quizSource: QuizHistoryEntry["quizSource"] =
    sourceRaw === "pdf_session" || sourceRaw === "text_summary" ? sourceRaw : "other";
  const total =
    Math.max(0, Math.round(Number(submission.total_question_count ?? 0))) ||
    params.quizQuestionsLength ||
    Math.max(
      1,
      Math.round(
        Number(submission.total_correct ?? 0) +
          Number(submission.total_wrong ?? 0) +
          Number(submission.total_unanswered ?? 0)
      )
    );
  const recommendedMiniCount = Math.max(
    1,
    Math.min(
      params.maxMiniQuizQuestions,
      Math.round(Number(submission.recommended_mini_quiz_count ?? 3) || 3)
    )
  );
  const topicRows = (submission.topic_analysis?.topics || [])
    .map((t) => {
      const correct = Math.max(0, Math.round(Number(t.correct_count ?? 0)));
      const total = Math.max(0, Math.round(Number(t.total_attempts ?? 0)));
      const wrong = Math.max(0, Math.round(Number(t.wrong_count ?? Math.max(0, total - correct))));
      const pct = Math.max(0, Math.min(100, Math.round(Number(t.success_rate ?? 0) * 100)));
      return {
        topic: String(t.topic ?? "").trim(),
        correct,
        total,
        pct,
        status: String(t.status ?? "").trim() || "unknown",
        wrong,
      };
    })
    .filter((x) => x.topic.length > 0)
    .slice(0, 24);

  const errorSummary: Record<string, number> = {};
  for (const k of Object.keys(submission.error_type_summary || {})) {
    const n = Math.max(0, Math.round(Number((submission.error_type_summary || {})[k] ?? 0)));
    if (n > 0) {
      errorSummary[k] = n;
    }
  }

  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    solvedAtIso: new Date().toISOString(),
    quizKind: kind,
    quizSource,
    score: Number(submission.score_percentage ?? 0),
    correct: Math.max(0, Math.round(Number(submission.total_correct ?? 0))),
    wrong: Math.max(0, Math.round(Number(submission.total_wrong ?? 0))),
    unanswered: Math.max(0, Math.round(Number(submission.total_unanswered ?? 0))),
    totalQuestions: Math.max(1, total),
    durationSeconds:
      submission.total_duration_seconds == null
        ? null
        : Math.max(0, Math.round(Number(submission.total_duration_seconds) || 0)),
    recommendedMiniCount,
    suggestedMiniQuizTopic:
      submission.suggested_mini_quiz_topic && submission.suggested_mini_quiz_topic.trim()
        ? submission.suggested_mini_quiz_topic.trim()
        : null,
    focusTopics: buildHistoryFocusTopics(submission, suggestions),
    recommendationLines: buildHistoryRecommendations(submission, suggestions),
    topicRows,
    errorSummary,
  };
}
