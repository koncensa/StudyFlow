import { Injectable } from "@angular/core";
import {
  QuizGenerateResponse,
  QuizQuestion,
  QuizSubmissionRequest,
  QuizSubmissionResponse,
  SuggestionResponse,
} from "../models/types";
import { environment } from "../../environments/environment";
import { fetchQuizWithDeadline } from "../utils/quiz-generate-client";
import { isQuizDocumentGoneError, readApiError } from "../utils/api-error";
import { mapQuizGenerateFailure } from "../utils/quiz-generate-client";
import { normalizeQuizSubmissionResponse } from "../utils/quiz-submission-normalize";
import { ApiService } from "./api.service";

type AppLang = "en" | "tr";

export type GeneratePdfQuizResult =
  | { ok: true; questions: QuizQuestion[]; timeLimitSeconds: number }
  | { ok: false; reason: "document_gone" | "error"; message: string };

export type GenerateSummaryQuizResult =
  | { ok: true; questions: QuizQuestion[]; timeLimitSeconds: number }
  | { ok: false; message: string };

export type SubmitQuizResult =
  | { ok: true; submission: QuizSubmissionResponse; suggestions: SuggestionResponse | null }
  | { ok: false; message: string };

@Injectable({
  providedIn: "root",
})
export class QuizService {
  constructor(private api: ApiService) {}

  generateFromDocument(params: {
    documentId: string;
    userId: number;
    numQuestions: number;
    maxTopics: number;
    focusTopics: string[];
    challengeTopics?: string[];
    locale?: "en" | "tr";
    difficulty?: "beginner" | "normal" | "technical";
    quizKind?: string;
    variationSalt?: number;
  }): Promise<QuizGenerateResponse> {
    const variationSalt =
      params.variationSalt != null && Number.isFinite(params.variationSalt)
        ? Math.floor(params.variationSalt)
        : Math.floor(Date.now() % 2147483647);
    return fetchQuizWithDeadline(
      () =>
        this.api.generateQuizFromDocument(
          params.documentId,
          params.userId,
          params.numQuestions,
          params.maxTopics,
          params.focusTopics,
          {
            challengeTopics: params.challengeTopics,
            locale: params.locale,
            difficulty: params.difficulty,
            quizKind: params.quizKind,
            variationSalt,
          }
        ),
      params.numQuestions,
      environment.quizGenerateDeadlineMs
    );
  }

  generateFromSummary(params: {
    content: string;
    numQuestions: number;
    maxTopics: number;
    locale?: "en" | "tr";
    difficulty?: "beginner" | "normal" | "technical";
  }): Promise<QuizGenerateResponse> {
    return fetchQuizWithDeadline(
      () =>
        this.api.generateQuizFromContent(params.content, params.numQuestions, params.maxTopics, [], {
          locale: params.locale,
          difficulty: params.difficulty,
        }),
      params.numQuestions,
      environment.quizGenerateDeadlineMs
    );
  }

  submit(payload: QuizSubmissionRequest): Promise<QuizSubmissionResponse> {
    return this.api.submitQuiz(payload).toPromise();
  }

  getSuggestions(payload: QuizSubmissionRequest): Promise<SuggestionResponse> {
    return this.api.getSuggestions(payload).toPromise();
  }

  /**
   * Scores + persists the quiz only. Suggestions are loaded separately — `/quiz/suggestions` runs
   * coaching / optional Ollama and can take minutes, which used to leave the UI stuck on "Submitting…".
   */
  async submitWithSuggestions(payload: QuizSubmissionRequest): Promise<{
    submission: QuizSubmissionResponse | null;
    suggestions: SuggestionResponse | null;
  }> {
    const raw = await this.submit(payload);
    const submission = normalizeQuizSubmissionResponse(raw);
    return { submission, suggestions: null };
  }

  /** Optional follow-up; safe to fire-and-forget after navigation. */
  async fetchSuggestionsSafe(payload: QuizSubmissionRequest): Promise<SuggestionResponse | null> {
    try {
      return await this.getSuggestions(payload);
    } catch {
      return null;
    }
  }

  async runPdfQuiz(params: {
    documentId: string;
    userId: number;
    numQuestions: number;
    maxTopics: number;
    focusTopics: string[];
    challengeTopics?: string[];
    locale: AppLang;
    difficulty: "beginner" | "normal" | "technical";
    quizKind: string;
    variationSalt?: number;
  }): Promise<GeneratePdfQuizResult> {
    try {
      const res = await this.generateFromDocument({
        documentId: params.documentId,
        userId: params.userId,
        numQuestions: params.numQuestions,
        maxTopics: params.maxTopics,
        focusTopics: params.focusTopics,
        challengeTopics: params.challengeTopics,
        locale: params.locale,
        difficulty: params.difficulty,
        quizKind: params.quizKind,
        variationSalt: params.variationSalt,
      });
      const tl = res?.time_limit_seconds;
      return {
        ok: true,
        questions: res.questions || [],
        timeLimitSeconds: typeof tl === "number" && tl > 0 ? tl : 0,
      };
    } catch (e: unknown) {
      if (isQuizDocumentGoneError(e)) {
        return { ok: false, reason: "document_gone", message: "" };
      }
      return { ok: false, reason: "error", message: mapQuizGenerateFailure(e) };
    }
  }

  async runSummaryQuiz(params: {
    draft: string;
    numQuestions: number;
    locale: AppLang;
  }): Promise<GenerateSummaryQuizResult> {
    const raw = (params.draft || "").trim();
    if (raw.length < 80) {
      return {
        ok: false,
        message:
          params.locale === "tr"
            ? "Özet en az ~80 karakter olmalı; biraz daha metin ekleyin."
            : "Summary text should be at least ~80 characters; paste a bit more content.",
      };
    }
    try {
      const res = await this.generateFromSummary({
        content: raw,
        numQuestions: params.numQuestions,
        maxTopics: 6,
        locale: params.locale,
        difficulty: "normal",
      });
      if (!res?.questions?.length) {
        return {
          ok: false,
          message: params.locale === "tr" ? "Quiz oluşturulamadı." : "Could not generate quiz from this text.",
        };
      }
      const tl = res?.time_limit_seconds;
      return {
        ok: true,
        questions: res.questions,
        timeLimitSeconds: typeof tl === "number" && tl > 0 ? tl : 0,
      };
    } catch (e: unknown) {
      return {
        ok: false,
        message: readApiError(
          e,
          params.locale === "tr" ? "Quiz oluşturulamadı." : "Could not generate quiz."
        ),
      };
    }
  }

  async runSubmitQuiz(params: {
    payload: QuizSubmissionRequest;
    locale: AppLang;
  }): Promise<SubmitQuizResult> {
    try {
      const result = await this.submitWithSuggestions(params.payload);
      if (!result.submission) {
        return {
          ok: false,
          message:
            params.locale === "tr" ? "Sunucu yanıtı işlenemedi." : "Could not parse server response.",
        };
      }
      return {
        ok: true,
        submission: result.submission,
        suggestions: result.suggestions,
      };
    } catch (e: unknown) {
      return { ok: false, message: readApiError(e, "Could not submit quiz.") };
    }
  }
}
