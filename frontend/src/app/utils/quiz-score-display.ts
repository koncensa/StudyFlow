/**
 * Single source for “quiz %” shown in UI: matches backend rule
 * (correct_count / total_question_count * 100), never a standalone random number.
 */

export function quizAttemptQuestionTotal(s: {
  total_question_count?: number | null;
  question_feedback?: { length: number } | null;
  total_correct: number;
  total_wrong: number;
  total_unanswered?: number | null;
}): number {
  const fromCount = Math.max(0, Math.round(Number(s.total_question_count) || 0));
  if (fromCount > 0) {
    return fromCount;
  }
  const fromFb = s.question_feedback?.length ?? 0;
  if (fromFb > 0) {
    return fromFb;
  }
  return Math.max(
    0,
    Math.round(Number(s.total_correct) || 0) +
      Math.round(Number(s.total_wrong) || 0) +
      Math.round(Number(s.total_unanswered) || 0),
  );
}

/** Percent with up to 2 decimals, same rounding spirit as server `round(..., 2)`. */
export function quizScorePercentFromAttempt(s: {
  total_correct: number;
  total_wrong: number;
  total_unanswered?: number | null;
  total_question_count?: number | null;
  question_feedback?: { length: number } | null;
  score_percentage?: number | null;
}): number {
  const tq = quizAttemptQuestionTotal(s);
  const c = Math.max(0, Math.round(Number(s.total_correct) || 0));
  if (tq > 0) {
    return Math.round((c / tq) * 10000) / 100;
  }
  const fallback = Number(s.score_percentage);
  return Number.isFinite(fallback) ? Math.round(fallback * 100) / 100 : 0;
}
