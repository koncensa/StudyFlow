import { Injectable } from "@angular/core";
import {
  LastQuizSummary,
  QuizQuestion,
  QuizResultsPageResponse,
  QuizSubmissionResponse,
  SuggestionResponse,
} from "../models/types";
import {
  assistantInsightTipLines,
  assistantQuizChallengeTopics,
  assistantQuizFocusTopics,
  assistantQuizStrongRaw,
  assistantQuizWeakRaw,
  lastQuizSummary,
  miniQuizFocusTopicsFromLastAttempt,
  mistakeTopicsFromSubmission,
  normalizeQuizQuestionsForApi,
  quizSubmissionForAnalysis,
} from "../utils/quiz-helper";

@Injectable({
  providedIn: "root",
})
export class AppFlowFacade {
  assistantQuizFocus(
    submission: QuizSubmissionResponse | null,
    suggestions: SuggestionResponse | null
  ): string[] {
    return assistantQuizFocusTopics(submission, suggestions);
  }

  assistantQuizChallenge(
    submission: QuizSubmissionResponse | null,
    suggestions: SuggestionResponse | null
  ): string[] {
    return assistantQuizChallengeTopics(submission, suggestions);
  }

  assistantWeakRaw(submission: QuizSubmissionResponse | null): string[] {
    return assistantQuizWeakRaw(submission);
  }

  assistantStrongRaw(submission: QuizSubmissionResponse | null): string[] {
    return assistantQuizStrongRaw(submission);
  }

  assistantTipLines(suggestions: SuggestionResponse | null): string[] {
    return assistantInsightTipLines(suggestions);
  }

  normalizeQuizQuestions(questions: QuizQuestion[]): QuizQuestion[] {
    return normalizeQuizQuestionsForApi(questions);
  }

  buildLastQuizSummary(
    quizSubmission: QuizSubmissionResponse | null,
    persistedResults: QuizResultsPageResponse | null
  ): LastQuizSummary | null {
    return lastQuizSummary(quizSubmission, persistedResults);
  }

  buildSubmissionForAnalysis(
    quizSubmission: QuizSubmissionResponse | null,
    persistedResults: QuizResultsPageResponse | null
  ): QuizSubmissionResponse | null {
    return quizSubmissionForAnalysis(quizSubmission, persistedResults);
  }

  miniQuizFocusTopics(
    currentSubmission: QuizSubmissionResponse | null,
    analysisSubmission: QuizSubmissionResponse | null
  ): string[] | null {
    return miniQuizFocusTopicsFromLastAttempt(currentSubmission, analysisSubmission);
  }

  mistakeTopics(submission: QuizSubmissionResponse | null): string[] {
    return mistakeTopicsFromSubmission(submission);
  }
}
