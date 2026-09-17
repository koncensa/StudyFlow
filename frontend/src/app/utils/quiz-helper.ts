import {
  LastQuizSummary,
  QuizQuestion,
  QuizResultsPageResponse,
  QuizSubmissionResponse,
  SuggestionResponse,
  TopicAnalysisResponse,
} from "../models/types";
import { extractWrongAnswers, remedialTopicNames } from "./quiz-results-pipeline";
import { quizAttemptQuestionTotal, quizScorePercentFromAttempt } from "./quiz-score-display";
import { coerceTopicAnalysis } from "./quiz-submission-normalize";

export function assistantQuizFocusTopics(
  submission: QuizSubmissionResponse | null,
  suggestions: SuggestionResponse | null
): string[] {
  const weak = remedialTopicNames(submission?.topic_analysis ?? null);
  const plan = suggestions?.adaptive_plan?.next_quiz_focus || [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of [...weak, ...plan]) {
    const s = String(t).trim();
    if (s && !seen.has(s.toLowerCase())) {
      seen.add(s.toLowerCase());
      out.push(s);
    }
    if (out.length >= 8) {
      break;
    }
  }
  return out;
}

export function assistantQuizChallengeTopics(
  submission: QuizSubmissionResponse | null,
  suggestions: SuggestionResponse | null
): string[] {
  const strong = submission?.topic_analysis?.strong_topics || [];
  const ch = suggestions?.adaptive_plan?.challenge_topics || [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of [...ch, ...strong]) {
    const s = String(t).trim();
    if (s && !seen.has(s.toLowerCase())) {
      seen.add(s.toLowerCase());
      out.push(s);
    }
    if (out.length >= 6) {
      break;
    }
  }
  return out;
}

export function assistantQuizWeakRaw(submission: QuizSubmissionResponse | null): string[] {
  const ta = submission?.topic_analysis;
  const merged = [...(ta?.very_weak_topics || []), ...(ta?.weak_topics || [])];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of merged) {
    const s = String(t).trim();
    const k = s.toLowerCase();
    if (s && !seen.has(k)) {
      seen.add(k);
      out.push(s);
    }
    if (out.length >= 10) {
      break;
    }
  }
  return out;
}

export function assistantQuizStrongRaw(submission: QuizSubmissionResponse | null): string[] {
  const strong = submission?.topic_analysis?.strong_topics;
  return Array.isArray(strong)
    ? strong.map((t) => String(t).trim()).filter(Boolean).slice(0, 8)
    : [];
}

export function assistantInsightTipLines(suggestions: SuggestionResponse | null): string[] {
  const lines: string[] = [];
  const coach = suggestions?.coach_message?.trim();
  if (coach) {
    lines.push(coach);
  }
  const acts = suggestions?.general_actions || [];
  for (const a of acts) {
    const m = a.message?.trim();
    if (m) {
      lines.push(m);
    }
    if (lines.length >= 4) {
      break;
    }
  }
  return lines.slice(0, 4);
}

export function normalizeQuizQuestionsForApi(
  questions: QuizQuestion[]
): QuizQuestion[] {
  return questions.map((q, i) => {
    const opts = (q.options || []).map((o) => String(o).trim()).filter((s) => s.length > 0);
    const qt =
      q.type != null && String(q.type).trim()
        ? String(q.type).trim().slice(0, 32).toLowerCase()
        : null;
    const allowed = new Set(["definition", "concept", "comparison", "application"]);
    const dRaw =
      q.difficulty != null && String(q.difficulty).trim()
        ? String(q.difficulty).trim().toLowerCase()
        : "";
    const diff =
      dRaw === "beginner" || dRaw === "easy"
        ? "beginner"
        : dRaw === "technical" || dRaw === "hard"
        ? "technical"
        : dRaw === "normal" || dRaw === "medium"
        ? "normal"
        : "";
    const idRaw = String(q.id ?? "").trim();
    const id = idRaw.length >= 8 ? idRaw.slice(0, 48) : `idx:${i}`;
    return {
      id,
      question_text: String(q.question_text || "").trim(),
      options: opts.slice(0, 6),
      correct_answer: String(q.correct_answer || "").trim(),
      topic: (String(q.topic || "").trim() || "General").slice(0, 200),
      ...(qt && allowed.has(qt) ? { type: qt } : {}),
      ...(diff ? { difficulty: diff } : {}),
      explanation:
        q.explanation != null && String(q.explanation).trim()
          ? String(q.explanation).trim().slice(0, 1200)
          : null,
      source_section:
        q.source_section != null && String(q.source_section).trim()
          ? String(q.source_section).trim().slice(0, 300)
          : null,
    };
  });
}

export function lastQuizSummary(
  quizSubmission: QuizSubmissionResponse | null,
  persistedResults: QuizResultsPageResponse | null
): LastQuizSummary | null {
  if (quizSubmission) {
    const s = quizSubmission;
    const tq = quizAttemptQuestionTotal({
      total_question_count: s.total_question_count,
      question_feedback: s.question_feedback,
      total_correct: s.total_correct,
      total_wrong: s.total_wrong,
      total_unanswered: s.total_unanswered,
    });
    const score = quizScorePercentFromAttempt({
      total_correct: s.total_correct,
      total_wrong: s.total_wrong,
      total_unanswered: s.total_unanswered,
      total_question_count: tq,
      question_feedback: s.question_feedback,
      score_percentage: s.score_percentage,
    });
    return {
      correct: s.total_correct,
      wrong: s.total_wrong,
      unanswered: s.total_unanswered ?? 0,
      totalQuestions: tq,
      score,
      durationSeconds: s.total_duration_seconds ?? null,
    };
  }
  const p = persistedResults;
  if (p?.has_data) {
    const tq = quizAttemptQuestionTotal({
      total_question_count: p.total_question_count,
      question_feedback: p.question_feedback,
      total_correct: p.total_correct,
      total_wrong: p.total_wrong,
      total_unanswered: p.total_unanswered,
    });
    const score = quizScorePercentFromAttempt({
      total_correct: p.total_correct,
      total_wrong: p.total_wrong,
      total_unanswered: p.total_unanswered,
      total_question_count: tq,
      question_feedback: p.question_feedback,
      score_percentage: p.score_percentage,
    });
    return {
      correct: p.total_correct,
      wrong: p.total_wrong,
      unanswered: p.total_unanswered ?? 0,
      totalQuestions: tq,
      score,
      durationSeconds: p.total_duration_seconds ?? null,
    };
  }
  return null;
}

export function quizSubmissionForAnalysis(
  quizSubmission: QuizSubmissionResponse | null,
  persistedResults: QuizResultsPageResponse | null
): QuizSubmissionResponse | null {
  if (quizSubmission) {
    return quizSubmission;
  }
  const p = persistedResults;
  if (!p?.has_data) {
    return null;
  }
  const tq = quizAttemptQuestionTotal({
    total_question_count: p.total_question_count,
    question_feedback: p.question_feedback,
    total_correct: p.total_correct,
    total_wrong: p.total_wrong,
    total_unanswered: p.total_unanswered,
  });
  const scorePct = quizScorePercentFromAttempt({
    total_correct: p.total_correct,
    total_wrong: p.total_wrong,
    total_unanswered: p.total_unanswered,
    total_question_count: tq,
    question_feedback: p.question_feedback,
    score_percentage: p.score_percentage,
  });
  return {
    attempt_id: p.attempt_id ?? null,
    total_correct: p.total_correct,
    total_wrong: p.total_wrong,
    total_unanswered: p.total_unanswered ?? 0,
    total_question_count: tq,
    total_duration_seconds: p.total_duration_seconds ?? null,
    quiz_source: null,
    score_percentage: scorePct,
    performance_comment: p.performance_comment ?? null,
    recommended_mini_quiz_count: p.recommended_mini_quiz_count,
    topic_analysis: p.topic_analysis ?? coerceTopicAnalysis(null),
    question_feedback: p.question_feedback ?? [],
    listed_wrong_count: p.listed_wrong_count,
    wrong_items: p.wrong_items ?? [],
    weak_topics: p.weak_topics ?? [],
    suggested_mini_quiz_topic: p.suggested_mini_quiz_topic ?? null,
    error_type_summary: p.error_type_summary ?? {},
    confused_topics_ranked: p.confused_topics_ranked ?? [],
    follow_up_actions: [],
    user_quiz_memory: p.user_quiz_memory ?? null,
    learning_brief: p.learning_brief ?? null,
  };
}

export function miniQuizFocusTopicsFromLastAttempt(
  currentSubmission: QuizSubmissionResponse | null,
  analysisSubmission: QuizSubmissionResponse | null
): string[] | null {
  const sub = currentSubmission ?? analysisSubmission;
  if (!sub) {
    return null;
  }
  const suggested = (sub.suggested_mini_quiz_topic || "").trim();
  if (suggested) {
    return [suggested];
  }
  const ta: TopicAnalysisResponse | null = sub.topic_analysis ?? null;
  const vw = (ta?.very_weak_topics || []).map((x) => String(x).trim()).filter(Boolean);
  if (vw.length) {
    return [vw[0]];
  }
  const wk = (ta?.weak_topics || []).map((x) => String(x).trim()).filter(Boolean);
  if (wk.length) {
    return [wk[0]];
  }
  const rows = extractWrongAnswers(sub);
  const fromWrong = rows.map((r) => (r.topic || "").trim()).filter(Boolean);
  if (fromWrong.length) {
    return [fromWrong[0]];
  }
  const weak = remedialTopicNames(ta ?? null);
  return weak.length ? [weak[0]] : null;
}

export function mistakeTopicsFromSubmission(
  submission: QuizSubmissionResponse | null
): string[] {
  if (!submission) {
    return [];
  }
  return [...new Set(extractWrongAnswers(submission).map((r) => r.topic).filter(Boolean))].slice(0, 8);
}
