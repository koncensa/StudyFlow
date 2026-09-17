import { Injectable } from "@angular/core";
import { HttpClient, HttpParams } from "@angular/common/http";
import { Observable } from "rxjs";
import { timeout } from "rxjs/operators";

/** Quiz generation can run several minutes (large N + local Ollama). */
const QUIZ_GENERATE_TIMEOUT_MS = 900000;
/** Score + DB write; allow headroom for slow local MySQL. */
const QUIZ_SUBMIT_TIMEOUT_MS = 180000;
/**
 * Suggestions re-score on the server then run coaching / optional Ollama — can hang without a cap,
 * which leaves the quiz UI stuck on "Submitting…".
 */
const QUIZ_SUGGESTIONS_TIMEOUT_MS = 120000;
/** GET /quiz/user-results — keep bounded so Results/Analysis tabs cannot spin forever. */
const QUIZ_USER_RESULTS_TIMEOUT_MS = 120000;

import { environment } from "../../environments/environment";
import { logQuizDocumentFlow } from "../utils/document-id";
import {
  ExplainLevel,
  PdfAssistantUploadResponse,
  PdfApplyModeResponseBody,
  PdfDynamicSummaryResponseBody,
  PdfChatResponseBody,
  PdfSessionHistoryResponse,
  PomodoroEndResponse,
  PomodoroStartResponse,
  PomodoroStatsResponse,
  ProfileResponse,
  QuizGenerateResponse,
  QuizHistoryListResponse,
  QuizQuestion,
  QuizResultsPageResponse,
  QuizSubmissionRequest,
  QuizSubmissionResponse,
  StudyChatMode,
  StudyOutcomeKind,
  StudyOutcomeResponse,
  SummaryFormat,
  SummaryLength,
  SummaryStyle,
  PdfDocumentStatusResponse,
  StudyStats,
  SuggestionResponse,
  BestResourcesResponse,
  AcademicPlanGenerateRequest,
  AcademicPlanHistoryResponse,
  AcademicPlanLatestResponse,
  AcademicPlanView,
  UserMeResponse,
} from "../models/types";

@Injectable({
  providedIn: "root",
})
export class ApiService {
  private base = environment.apiBaseUrl;

  constructor(private http: HttpClient) {}

  private toSummaryMode(studyMode?: StudyChatMode): "summary" | "explain" | "exam" {
    const raw = (studyMode || "quick_summary").toLowerCase();
    if (raw === "explain_simple") return "explain";
    if (raw === "exam_focus") return "exam";
    return "summary";
  }
//apiye gönderim
  uploadPdf(
    file: File,
    createQuiz: boolean,
    numQuestions: number,
    userId: number,
    outputLocale: "en" | "tr" = "en",
    useLegacySummary = false
  ): Observable<PdfAssistantUploadResponse> {
    const form = new FormData();
    form.append("file", file);
    form.append("create_quiz", createQuiz ? "true" : "false");
    form.append("num_questions", String(numQuestions));
    form.append("user_id", String(userId));
    form.append("output_locale", outputLocale);
    form.append("use_legacy_summary", useLegacySummary ? "true" : "false");

    return this.http.post<PdfAssistantUploadResponse>(`${this.base}/pdf/upload-pdf`, form);
  }

  pdfChat(
    documentId: string,
    messages: { role: "user" | "assistant"; content: string }[],
    userId: number,
    studyMode?: StudyChatMode,
    explainLevel?: ExplainLevel,
    outputLocale: "en" | "tr" = "en",
    opts?: { focusTopics?: string[]; challengeTopics?: string[] }
  ): Observable<PdfChatResponseBody> {
    return this.http.post<PdfChatResponseBody>(`${this.base}/pdf/chat`, {
      document_id: documentId,
      user_id: userId,
      messages,
      study_mode: studyMode || "tutor_chat",
      explain_level: explainLevel || "normal",
      output_locale: outputLocale,
      focus_topics: opts?.focusTopics?.length ? opts.focusTopics : [],
      challenge_topics: opts?.challengeTopics?.length ? opts.challengeTopics : [],
    });
  }

  applyPdfStudyMode(
    documentId: string,
    userId: number,
    studyMode: StudyChatMode,
    explainLevel: ExplainLevel,
    outputLocale: "en" | "tr",
    opts?: {
      focusTopics?: string[];
      challengeTopics?: string[];
      summaryStyle?: SummaryStyle;
      summaryFormat?: SummaryFormat;
      summaryLength?: SummaryLength;
    }
  ): Observable<PdfApplyModeResponseBody> {
    const mode = this.toSummaryMode(studyMode);
    return this.http.post<PdfApplyModeResponseBody>(`${this.base}/pdf/apply-mode`, {
      document_id: documentId,
      user_id: userId,
      study_mode: studyMode,
      mode,
      explain_level: explainLevel,
      output_locale: outputLocale,
      focus_topics: opts?.focusTopics?.length ? opts.focusTopics : [],
      challenge_topics: opts?.challengeTopics?.length ? opts.challengeTopics : [],
      summary_style: opts?.summaryStyle || "balanced",
      summary_format: opts?.summaryFormat || "mixed",
      summary_length: opts?.summaryLength || "medium",
    });
  }

  generateDynamicPdfSummary(
    documentId: string,
    userId: number,
    options?: {
      studyMode?: StudyChatMode;
      explainLevel?: ExplainLevel;
      outputLocale?: "en" | "tr";
      focusTopics?: string[];
      challengeTopics?: string[];
      summaryStyle?: SummaryStyle;
      summaryFormat?: SummaryFormat;
      summaryLength?: SummaryLength;
      includeQuiz?: boolean;
      numQuestions?: number;
      maxTopics?: number;
      quizDifficulty?: ExplainLevel;
      quizKind?: string;
    }
  ): Observable<PdfDynamicSummaryResponseBody> {
    const studyMode = options?.studyMode || "quick_summary";
    const mode = this.toSummaryMode(studyMode);
    return this.http.post<PdfDynamicSummaryResponseBody>(`${this.base}/pdf/generate-summary`, {
      document_id: documentId,
      user_id: userId,
      study_mode: studyMode,
      mode,
      explain_level: options?.explainLevel || "normal",
      output_locale: options?.outputLocale || "en",
      focus_topics: options?.focusTopics?.length ? options.focusTopics : [],
      challenge_topics: options?.challengeTopics?.length ? options.challengeTopics : [],
      summary_style: options?.summaryStyle || "balanced",
      summary_format: options?.summaryFormat || "mixed",
      summary_length: options?.summaryLength || "medium",
      include_quiz: !!options?.includeQuiz,
      num_questions: Math.max(1, Math.min(15, Math.round(options?.numQuestions || 5))),
      max_topics: Math.max(1, Math.min(15, Math.round(options?.maxTopics || 6))),
      quiz_difficulty: options?.quizDifficulty || options?.explainLevel || "normal",
      quiz_kind: options?.quizKind || "standard",
    });
  }

  studyOutcome(
    documentId: string,
    userId: number,
    outcome: StudyOutcomeKind,
    explainLevel: ExplainLevel = "normal"
  ): Observable<StudyOutcomeResponse> {
    return this.http.post<StudyOutcomeResponse>(`${this.base}/pdf/study-outcome`, {
      document_id: documentId,
      user_id: userId,
      outcome,
      explain_level: explainLevel,
    });
  }

  checkPdfDocumentStatus(documentId: string, userId: number): Observable<PdfDocumentStatusResponse> {
    return this.http.post<PdfDocumentStatusResponse>(`${this.base}/pdf/document-status`, {
      document_id: documentId,
      user_id: userId,
    });
  }

  /** PDF assistant chat sessions for the authenticated user only. */
  getPdfSessionHistory(limit = 32): Observable<PdfSessionHistoryResponse> {
    return this.http.get<PdfSessionHistoryResponse>(`${this.base}/pdf/session-history`, {
      params: { limit: String(Math.max(1, Math.min(64, Math.round(limit)))) },
    });
  }

  generateQuizFromDocument(
    documentId: string,
    userId: number,
    numQuestions: number,
    maxTopics: number,
    focusTopics: string[] = [],
    opts?: {
      challengeTopics?: string[];
      locale?: "en" | "tr";
      difficulty?: "beginner" | "normal" | "technical";
      quizKind?: string;
      useTopicAdaptive?: boolean;
      /** Her üretimde farklı soru açıları için (backend excerpt / sıra rotasyonu + yasak kökler). */
      variationSalt?: number;
    }
  ): Observable<QuizGenerateResponse> {
    const salt =
      opts?.variationSalt != null && Number.isFinite(opts.variationSalt)
        ? Math.max(0, Math.min(2147483647, Math.floor(opts.variationSalt)))
        : Math.floor(Date.now() % 2147483647);
    const body = {
      document_id: documentId,
      user_id: userId,
      num_questions: numQuestions,
      max_topics: maxTopics,
      focus_topics: focusTopics,
      challenge_topics: opts?.challengeTopics?.length ? opts.challengeTopics : [],
      locale: opts?.locale || "en",
      difficulty: opts?.difficulty || "normal",
      variation_salt: salt,
      ...(opts?.quizKind ? { quiz_kind: opts.quizKind } : {}),
      ...(opts?.useTopicAdaptive === false ? { use_topic_adaptive: false } : {}),
    };
    logQuizDocumentFlow("quiz API request body (full payload)", {
      ...body,
      document_id_prefix: documentId.slice(0, 16),
    });
    return this.http
      .post<QuizGenerateResponse>(`${this.base}/quiz/generate-from-document`, body)
      .pipe(timeout(QUIZ_GENERATE_TIMEOUT_MS));
  }

  getProfile(): Observable<ProfileResponse> {
    return this.http.get<ProfileResponse>(`${this.base}/study/profile`);
  }

  deleteMyAccount(): Observable<void> {
    return this.http.delete<void>(`${this.base}/auth/me`);
  }

  getMe(): Observable<UserMeResponse> {
    return this.http.get<UserMeResponse>(`${this.base}/auth/me`);
  }

  patchMe(payload: { username: string | null; biography: string | null }): Observable<UserMeResponse> {
    return this.http.patch<UserMeResponse>(`${this.base}/auth/me`, payload);
  }

  changePassword(payload: {
    current_password: string;
    new_password: string;
    confirm_new_password: string;
  }): Observable<UserMeResponse> {
    return this.http.post<UserMeResponse>(`${this.base}/auth/me/password`, payload);
  }

  uploadProfilePhoto(file: File): Observable<UserMeResponse> {
    const form = new FormData();
    form.append("file", file, file.name);
    return this.http.post<UserMeResponse>(`${this.base}/auth/me/profile-photo`, form);
  }

  deleteProfilePhoto(): Observable<UserMeResponse> {
    return this.http.delete<UserMeResponse>(`${this.base}/auth/me/profile-photo`);
  }

  updateProfileLocale(locale: string): Observable<ProfileResponse> {
    return this.http.patch<ProfileResponse>(`${this.base}/study/profile/locale`, { locale });
  }

  generateQuizFromContent(
    content: string,
    numQuestions: number,
    maxTopics: number,
    focusTopics: string[] = [],
    opts?: {
      challengeTopics?: string[];
      locale?: "en" | "tr";
      difficulty?: "beginner" | "normal" | "technical";
    }
  ): Observable<QuizGenerateResponse> {
    return this.http
      .post<QuizGenerateResponse>(`${this.base}/quiz/generate`, {
        content,
        num_questions: numQuestions,
        max_topics: maxTopics,
        focus_topics: focusTopics,
        challenge_topics: opts?.challengeTopics?.length ? opts.challengeTopics : [],
        locale: opts?.locale || "en",
        difficulty: opts?.difficulty || "normal",
      })
      .pipe(timeout(QUIZ_GENERATE_TIMEOUT_MS));
  }

  submitQuiz(payload: QuizSubmissionRequest): Observable<QuizSubmissionResponse> {
    return this.http
      .post<QuizSubmissionResponse>(`${this.base}/quiz/submit`, payload)
      .pipe(timeout(QUIZ_SUBMIT_TIMEOUT_MS));
  }

  getSuggestions(payload: QuizSubmissionRequest): Observable<SuggestionResponse> {
    return this.http
      .post<SuggestionResponse>(`${this.base}/quiz/suggestions`, payload)
      .pipe(timeout(QUIZ_SUGGESTIONS_TIMEOUT_MS));
  }

  getStudyStats(): Observable<StudyStats> {
    return this.http.get<StudyStats>(`${this.base}/stats/summary`);
  }

  getQuizUserResults(locale: "en" | "tr" = "en"): Observable<QuizResultsPageResponse> {
    const params = new HttpParams().set("locale", locale);
    return this.http
      .get<QuizResultsPageResponse>(`${this.base}/quiz/user-results`, { params })
      .pipe(timeout(QUIZ_USER_RESULTS_TIMEOUT_MS));
  }

  getQuizHistory(limit = 60): Observable<QuizHistoryListResponse> {
    const params = new HttpParams().set("limit", String(limit));
    return this.http.get<QuizHistoryListResponse>(`${this.base}/quiz/history`, { params });
  }

  getBestResources(topic: string, locale: "en" | "tr" = "en"): Observable<BestResourcesResponse> {
    let params = new HttpParams().set("locale", locale);
    if (topic && topic.trim()) {
      params = params.set("topic", topic.trim());
    }
    return this.http.get<BestResourcesResponse>(`${this.base}/study/best-resources`, { params });
  }

  pomodoroStart(payload: { user_id: number; topic?: string | null; start_time?: string | null }): Observable<PomodoroStartResponse> {
    return this.http.post<PomodoroStartResponse>(`${this.base}/pomodoro/start`, payload);
  }

  pomodoroEnd(payload: { session_id: number; end_time?: string | null }): Observable<PomodoroEndResponse> {
    return this.http.post<PomodoroEndResponse>(`${this.base}/pomodoro/end`, payload);
  }

  pomodoroCancel(payload: { session_id: number; end_time?: string | null }): Observable<PomodoroEndResponse> {
    return this.http.post<PomodoroEndResponse>(`${this.base}/pomodoro/cancel`, payload);
  }

  pomodoroStats(): Observable<PomodoroStatsResponse> {
    return this.http.get<PomodoroStatsResponse>(`${this.base}/pomodoro/stats`);
  }

  getAcademicPlanLatest(asOf?: string): Observable<AcademicPlanLatestResponse> {
    let params = new HttpParams();
    if (asOf) {
      params = params.set("as_of", asOf);
    }
    return this.http.get<AcademicPlanLatestResponse>(`${this.base}/study/academic-plan/latest`, { params });
  }

  generateAcademicPlan(body: AcademicPlanGenerateRequest, asOf?: string): Observable<AcademicPlanView> {
    let params = new HttpParams();
    if (asOf) {
      params = params.set("as_of", asOf);
    }
    return this.http.post<AcademicPlanView>(`${this.base}/study/academic-plan/generate`, body, { params });
  }

  patchAcademicPlanTask(
    planId: number,
    taskId: string,
    status: "pending" | "completed" | "missed",
    asOf?: string
  ): Observable<AcademicPlanView> {
    let params = new HttpParams();
    if (asOf) {
      params = params.set("as_of", asOf);
    }
    return this.http.patch<AcademicPlanView>(`${this.base}/study/academic-plan/${planId}/tasks/${taskId}`, { status }, { params });
  }

  deleteAcademicPlan(planId: number): Observable<void> {
    return this.http.delete<void>(`${this.base}/study/academic-plan/${planId}`);
  }

  finishAcademicPlan(planId: number, asOf?: string): Observable<AcademicPlanView> {
    let params = new HttpParams();
    if (asOf) {
      params = params.set("as_of", asOf);
    }
    return this.http.post<AcademicPlanView>(`${this.base}/study/academic-plan/${planId}/finish`, {}, { params });
  }

  getAcademicPlanHistory(): Observable<AcademicPlanHistoryResponse> {
    return this.http.get<AcademicPlanHistoryResponse>(`${this.base}/study/academic-plan/history`);
  }

  getAcademicPlanHistoryItem(planId: number, asOf?: string): Observable<AcademicPlanView> {
    let params = new HttpParams();
    if (asOf) {
      params = params.set("as_of", asOf);
    }
    return this.http.get<AcademicPlanView>(`${this.base}/study/academic-plan/history/${planId}`, { params });
  }
}
