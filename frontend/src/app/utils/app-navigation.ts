import { AppNavKey } from "./app-view-text";
import { QuizKind } from "./quiz-history";

export interface GoNavOptions {
  allowQuizSession?: boolean;
  bypassQuizNavLock?: boolean;
}

export interface GoNavDecision {
  requiresAuthDialog: boolean;
  blockNavigation: boolean;
  resetQuizComposer: boolean;
  refreshResults: boolean;
  setResultsTabLatest: boolean;
  refreshStats: boolean;
}

export function decideGoNavigation(params: {
  nav: AppNavKey;
  skipAuthCheck: boolean;
  isProtectedNav: boolean;
  isAuthenticated: boolean;
  options?: GoNavOptions;
  quizReady: boolean;
  hasQuizSubmission: boolean;
  activeQuizKind: QuizKind | null;
}): GoNavDecision {
  const { nav, skipAuthCheck, isProtectedNav, isAuthenticated, options, quizReady, hasQuizSubmission, activeQuizKind } =
    params;

  if (!skipAuthCheck && isProtectedNav && !isAuthenticated) {
    return {
      requiresAuthDialog: true,
      blockNavigation: true,
      resetQuizComposer: false,
      refreshResults: false,
      setResultsTabLatest: false,
      refreshStats: false,
    };
  }

  if (nav === "quiz" && isAuthenticated && !options?.bypassQuizNavLock) {
    return {
      requiresAuthDialog: false,
      blockNavigation: true,
      resetQuizComposer: false,
      refreshResults: false,
      setResultsTabLatest: false,
      refreshStats: false,
    };
  }

  const quizSessionActive = quizReady && !hasQuizSubmission;
  const resetQuizComposer =
    nav === "quiz" &&
    !options?.allowQuizSession &&
    (!quizSessionActive || activeQuizKind === "mini_adaptive");

  return {
    requiresAuthDialog: false,
    blockNavigation: false,
    resetQuizComposer,
    refreshResults: nav === "results" || nav === "analysis",
    setResultsTabLatest: nav === "results",
    refreshStats: nav === "stats",
  };
}
