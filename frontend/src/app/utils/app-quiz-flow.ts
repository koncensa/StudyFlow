import { QuizSubmissionResponse, TailoredMiniQuizPayload } from "../models/types";
import { quizScorePercentFromAttempt } from "./quiz-score-display";

type QuizDifficulty = "beginner" | "normal" | "technical";

function hashMiniSalt(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return h;
}

export function resolveMiniQuizCount(
  manualQuizGenCount: number,
  fallbackCount: number,
  maxMiniQuizQuestions: number
): number {
  const manualCount = Math.round(Number(manualQuizGenCount));
  const fallback = Math.round(Number(fallbackCount) || 3);
  const count = Number.isFinite(manualCount) && manualCount >= 1 ? manualCount : fallback;
  return Math.max(1, Math.min(maxMiniQuizQuestions, count));
}

function resolveTailoredDifficulty(
  requested: TailoredMiniQuizPayload["difficulty"],
  submission: QuizSubmissionResponse | null
): QuizDifficulty {
  let difficulty = requested;
  if (!difficulty || (difficulty !== "beginner" && difficulty !== "normal" && difficulty !== "technical")) {
    const scorePct = submission
      ? quizScorePercentFromAttempt({
          total_correct: submission.total_correct,
          total_wrong: submission.total_wrong,
          total_unanswered: submission.total_unanswered,
          total_question_count: submission.total_question_count,
          question_feedback: submission.question_feedback,
          score_percentage: submission.score_percentage,
        })
      : NaN;
    if (Number.isFinite(scorePct)) {
      if (scorePct < 55) {
        difficulty = "beginner";
      } else if (scorePct >= 80) {
        difficulty = "technical";
      } else {
        difficulty = "normal";
      }
    } else {
      difficulty = "normal";
    }
  }
  return difficulty;
}

function resolveTailoredChallengeTopics(params: {
  challengeTopicsFromEvent: string[];
  strongTopics: string[];
  focusTopics: string[];
  quizCount: number;
}): string[] | null {
  if (params.challengeTopicsFromEvent.length) {
    return params.challengeTopicsFromEvent.slice(0, 6);
  }
  if (!params.strongTopics.length || !params.focusTopics.length) {
    return null;
  }
  const salt = `${params.focusTopics.join("|")}#${params.quizCount}#${Date.now()}`;
  const scored = params.strongTopics.map((topic) => ({ topic, score: hashMiniSalt(topic + salt) }));
  scored.sort((a, b) => a.score - b.score);
  const picked = scored.slice(0, 2).map((x) => x.topic);
  return picked.length ? picked : null;
}

export function resolveTailoredMiniQuizConfig(params: {
  event: TailoredMiniQuizPayload;
  manualQuizGenCount: number;
  maxMiniQuizQuestions: number;
  submissionForScoring: QuizSubmissionResponse | null;
  strongTopics: string[];
}): {
  count: number;
  focusTopics: string[] | null;
  difficulty: QuizDifficulty;
  challengeTopics: string[] | null;
} {
  const count = resolveMiniQuizCount(params.manualQuizGenCount, params.event.count, params.maxMiniQuizQuestions);
  const topics = (params.event.topics || []).map((x) => String(x).trim()).filter(Boolean);
  const challengeFromEvent = (params.event.challengeTopics || []).map((x) => String(x).trim()).filter(Boolean);
  return {
    count,
    focusTopics: topics.length ? topics.slice(0, 8) : null,
    difficulty: resolveTailoredDifficulty(params.event.difficulty, params.submissionForScoring),
    challengeTopics: resolveTailoredChallengeTopics({
      challengeTopicsFromEvent: challengeFromEvent,
      strongTopics: params.strongTopics,
      focusTopics: topics,
      quizCount: count,
    }),
  };
}

export function resolveCoachingSuggestedQuizConfig(params: {
  numQuestions: unknown;
  difficulty: unknown;
  focusTopics: unknown;
  maxStandardQuizQuestions: number;
}): {
  count: number;
  difficulty: QuizDifficulty | null;
  focusTopics: string[] | null;
} {
  const count = Math.max(
    1,
    Math.min(params.maxStandardQuizQuestions, Math.round(Number(params.numQuestions) || 5))
  );
  const d = String(params.difficulty || "normal").toLowerCase();
  const difficulty = d === "beginner" || d === "technical" || d === "normal" ? d : null;
  const rawFocus = Array.isArray(params.focusTopics) ? params.focusTopics : [];
  const focusTopics = rawFocus.length
    ? rawFocus.map((x) => String(x).trim()).filter(Boolean).slice(0, 12)
    : null;
  return { count, difficulty, focusTopics };
}

export function resolveMistakesRetryCount(
  manualQuizGenCount: number,
  wrongTopicsLength: number,
  maxMiniQuizQuestions: number
): number {
  const manualCount = Math.round(Number(manualQuizGenCount));
  const count =
    Number.isFinite(manualCount) && manualCount >= 1
      ? manualCount
      : Math.max(1, Math.round(Number(wrongTopicsLength) || 1));
  return Math.min(maxMiniQuizQuestions, count);
}
