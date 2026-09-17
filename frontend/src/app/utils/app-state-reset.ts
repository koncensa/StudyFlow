import { QuizHistoryEntry } from "./quiz-history";

export function studyUiResetPatch(): Record<string, unknown> {
  return {
    quizQuestions: null,
    quizTimeLimitSeconds: 0,
    quizSource: "pdf_session",
    quizSummaryDraft: "",
    quizSummaryLoading: false,
    quizSubmission: null,
    activeQuizKind: null,
    suggestions: null,
    persistedResults: null,
    activePdfDocumentId: null,
    studyStats: null,
    statsRefreshing: false,
    statsRefreshError: null,
    uploadError: null,
    quizDifficultyOverride: null,
    quizChallengeOverride: null,
    resultsLoadError: null,
    quizHistory: [] as QuizHistoryEntry[],
    quizHistoryFilter: "all",
    academicPlanHistoryForProgress: [],
    badgeUnlockQueue: [],
    activeBadgeUnlock: null,
    badgeUnlockHydrated: false,
  };
}

export function quizComposerResetPatch(): Record<string, unknown> {
  return {
    quizQuestions: null,
    quizTimeLimitSeconds: 0,
    quizSubmission: null,
    suggestions: null,
    quizMiniMode: false,
    quizFocusOverride: null,
    quizDifficultyOverride: null,
    quizChallengeOverride: null,
    activeQuizKind: null,
    preparedPdfQuizPending: false,
    uploadError: null,
  };
}

export function retryMiniFromHistoryRequest(params: {
  manualQuizGenCount: number;
  recommendedMiniCount: number;
  focusTopics: string[];
  suggestedMiniQuizTopic: string | null;
  maxMiniQuizQuestions: number;
}): { count: number; topics: string[] } {
  const manualCount = Math.round(Number(params.manualQuizGenCount));
  const count =
    Number.isFinite(manualCount) && manualCount >= 1
      ? Math.max(1, Math.min(params.maxMiniQuizQuestions, manualCount))
      : Math.max(
          1,
          Math.min(params.maxMiniQuizQuestions, Math.round(Number(params.recommendedMiniCount) || 3))
        );
  const topics = params.focusTopics.length
    ? params.focusTopics
    : params.suggestedMiniQuizTopic
    ? [params.suggestedMiniQuizTopic]
    : [];
  return { count, topics };
}
