/**
 * Single view-model path for Results:
 * quizSubmission.wrong_items (or question_feedback) → wrongAnswer rows.
 */

import { QuizQuestionFeedback, QuizSubmissionResponse, TopicAnalysisResponse } from "../models/types";
import { feedbackRowIsCorrect, feedbackRowIsUnanswered } from "./quiz-submission-normalize";

export interface WrongAnswerRow {
  questionIndex: number;
  topic: string;
  questionText: string;
  userAnswer: string;
  correctAnswer: string;
  questionType?: string | null;
  whyWrong?: string | null;
  errorType?: string | null;
  confusedConcepts?: string[];
  teachingSnippet?: string | null;
  nextStep?: string | null;
  yourMistake?: string | null;
  whyIncorrect?: string | null;
  correctThinking?: string | null;
  correctAnswerExplained?: string | null;
}

function rowsFromWrongFeedback(wrongFb: QuizQuestionFeedback[]): WrongAnswerRow[] {
  return wrongFb
    .filter((f) => !feedbackRowIsUnanswered(f))
    .sort((a, b) => a.question_index - b.question_index)
    .map((f) => {
      const concepts = (f.confused_concepts || [])
        .map((c) => String(c || "").trim())
        .filter(Boolean);
      return {
        questionIndex: f.question_index,
        topic: (f.topic || "General").trim() || "General",
        questionText: (f.question_text != null ? String(f.question_text) : "").trim(),
        userAnswer: (f.user_answer != null && String(f.user_answer).trim() ? String(f.user_answer) : f.selected_answer) || "",
        correctAnswer: f.correct_answer,
        questionType: f.question_type != null && String(f.question_type).trim() ? String(f.question_type).trim() : null,
        whyWrong: f.why_wrong != null && String(f.why_wrong).trim() ? String(f.why_wrong).trim() : null,
        errorType: f.error_type,
        confusedConcepts: concepts.length ? concepts : undefined,
        teachingSnippet:
          f.teaching_snippet != null && String(f.teaching_snippet).trim() ? String(f.teaching_snippet).trim() : null,
        nextStep: f.hint != null && String(f.hint).trim() ? String(f.hint).trim() : null,
        yourMistake: f.your_mistake != null && String(f.your_mistake).trim() ? String(f.your_mistake).trim() : null,
        whyIncorrect: f.why_incorrect != null && String(f.why_incorrect).trim() ? String(f.why_incorrect).trim() : null,
        correctThinking:
          f.correct_thinking != null && String(f.correct_thinking).trim() ? String(f.correct_thinking).trim() : null,
        correctAnswerExplained:
          f.correct_answer_explained != null && String(f.correct_answer_explained).trim()
            ? String(f.correct_answer_explained).trim()
            : null,
      };
    });
}

/**
 * Prefer server `wrong_items` when it matches `total_wrong`; otherwise derive from `question_feedback`
 * with robust `is_correct` handling (e.g. string "false").
 */
export function extractWrongAnswers(submission: QuizSubmissionResponse | null | undefined): WrongAnswerRow[];
/** @deprecated Pass full QuizSubmissionResponse so wrong_items / is_correct coercion apply. */
export function extractWrongAnswers(feedback: QuizQuestionFeedback[] | null | undefined): WrongAnswerRow[];
export function extractWrongAnswers(
  subOrFeedback: QuizSubmissionResponse | QuizQuestionFeedback[] | null | undefined,
): WrongAnswerRow[] {
  if (!subOrFeedback) {
    return [];
  }
  if (Array.isArray(subOrFeedback)) {
    return rowsFromWrongFeedback(
      subOrFeedback.filter((f) => !feedbackRowIsCorrect(f) && !feedbackRowIsUnanswered(f)),
    );
  }
  const sub = subOrFeedback;
  const tw = sub.total_wrong ?? 0;
  const wi = sub.wrong_items;
  let wrongFb: QuizQuestionFeedback[];
  if (wi && wi.length === tw) {
    wrongFb = wi;
  } else {
    wrongFb = (sub.question_feedback || []).filter(
      (f) => !feedbackRowIsCorrect(f) && !feedbackRowIsUnanswered(f),
    );
  }
  return rowsFromWrongFeedback(wrongFb);
}

/** topic → rows (full list preserved). */
export function groupWrongByTopic(rows: WrongAnswerRow[]): Map<string, WrongAnswerRow[]> {
  const m = new Map<string, WrongAnswerRow[]>();
  for (const r of rows) {
    const key = r.topic || "General";
    if (!m.has(key)) {
      m.set(key, []);
    }
    m.get(key)!.push(r);
  }
  return m;
}

export function remedialTopicNames(ta: TopicAnalysisResponse | null | undefined): string[] {
  if (!ta) {
    return [];
  }
  const out: string[] = [];
  const vw = ta.very_weak_topics || [];
  const w = ta.weak_topics || [];
  for (const t of vw) {
    if (t && !out.includes(t)) {
      out.push(t);
    }
  }
  for (const t of w) {
    if (t && !out.includes(t)) {
      out.push(t);
    }
  }
  return out;
}

/** Optional: reconcile wrong count with submission totals (debug). */
export function wrongCountMatchesSubmission(sub: QuizSubmissionResponse | null | undefined): boolean {
  if (!sub) {
    return true;
  }
  const n = extractWrongAnswers(sub).length;
  return n === (sub.total_wrong ?? 0);
}
