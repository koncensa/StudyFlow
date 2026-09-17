import { Component, OnDestroy, OnInit } from "@angular/core";
import { Subscription } from "rxjs";
import { distinctUntilChanged } from "rxjs/operators";

import { CurrentUserService } from "./session/current-user.service";
import { ApiService } from "./services/api.service";
import {
  AcademicPlanView,
  AcademicPlanHistoryItem,
  LastQuizSummary,
  ProfileBadgeItem,
  ProfileResponse,
  QuizQuestion,
  QuizReadyPayload,
  QuizResultsPageResponse,
  QuizSubmissionRequest,
  QuizSubmissionResponse,
  QuizSubmitPayload,
  StudyStats,
  SuggestionResponse,
  TailoredMiniQuizPayload,
  TopicAnalysisResponse,
  TopicProgressItem,
} from "./models/types";
import { normalizeAppLang } from "./i18n/app-lang";
import { I18nService } from "./i18n/i18n.service";
import { readApiError } from "./utils/api-error";
import {
  logQuizDocumentFlow,
  normalizeDocumentId,
  stripDocumentFromLocalHistory,
} from "./utils/document-id";
import { quizGenerationPhaseMessage } from "./utils/quiz-loading-messages";
import { environment } from "../environments/environment";
import { PomodoroSessionService, PomodoroTimerService, PomodoroTimerSnapshot } from "./services/pomodoro";
import { AppFlowFacade } from "./facades/app-flow.facade";
import { QuizService } from "./services/quiz.service";
import { ResultsService } from "./services/results.service";
import { StudyService } from "./services/study.service";
import {
  clearQuizHistoryInStorage,
  buildQuizHistoryStorageKey,
  clearLegacyQuizHistoryStorage,
  loadQuizHistoryFromStorage,
  mapServerQuizHistoryItems,
  QuizHistoryEntry,
  QuizKind,
  saveQuizHistoryToStorage,
} from "./utils/quiz-history";
import {
  AppNavKey,
  badgeCompletionPercentForSections,
  badgeActionCtaText,
  badgeActionForSlug,
  badgeEarnedCountForSections,
  badgeGroupEarnedCountForSection,
  badgeTotalCountForSections,
  BadgeSection,
  buildBadgeSections,
  navClassFor,
  pageShellClassFor,
  quizKindLabelText,
  quizNavClassFor,
  quizShortCommentText,
  quizSourceLabelText,
} from "./utils/app-view-text";
import {
  formatProgressDurationSecondsText,
  hasLiveFocusIncrementValue,
  pomodoroTopicBreakdownRows,
  progressFocusSecondsValue,
  quizLifecycleBarRows,
} from "./utils/app-progress-view";
import {
  resolveCoachingSuggestedQuizConfig,
  resolveMiniQuizCount,
  resolveMistakesRetryCount,
  resolveTailoredMiniQuizConfig,
} from "./utils/app-quiz-flow";
import { resolveActiveQuizDocumentId } from "./utils/app-quiz-session";
import { decideGoNavigation, GoNavOptions } from "./utils/app-navigation";
import { resolveNewBadgeUnlocks } from "./utils/app-badge-unlock";
import { isUnauthorizedHttpError, profileFallbackEn } from "./utils/app-profile";
import { quizComposerResetPatch, retryMiniFromHistoryRequest, studyUiResetPatch } from "./utils/app-state-reset";

export type NavKey = AppNavKey;
type QuizHistoryFilter = "all" | "standard" | "mini";
const MAX_STANDARD_QUIZ_QUESTIONS = 15;
const MAX_MINI_QUIZ_QUESTIONS = 15;

@Component({
  selector: "app-root",
  templateUrl: "./app.component.html",
  styleUrls: ["./app.component.css"],
})
export class AppComponent implements OnInit, OnDestroy {
  // Main shell state for top navigation.
  activeNav: NavKey = "home"; // Current page tab.
  private userIdSubscription?: Subscription;
  private pomodoroEndedSubscription?: Subscription;
  private pomodoroStateSubscription?: Subscription;

  // Session modal controls (open state + selected tab + optional notice).
  sessionDialogOpen = false; // Login/Register dialog state.
  sessionDialogTab: "login" | "register" = "login";
  sessionDialogNotice: string | null = null;

  private readonly protectedNav = new Set<NavKey>(["pdf", "quiz", "results", "analysis", "plan", "pomo", "stats", "badges", "profile"]); // Tabs that need login.

  profile: ProfileResponse | null = null;
  badgeUnlockQueue: ProfileBadgeItem[] = [];
  activeBadgeUnlock: ProfileBadgeItem | null = null;
  private badgeUnlockTimer: number | null = null;
  private badgeUnlockHydrated = false;

  uploadError: string | null = null; // Top-level user-facing error text.

  quizQuestions: QuizQuestion[] | null = null; // Active quiz questions in memory.
  quizTimeLimitSeconds = 0;
  activePdfDocumentId: string | null = null;
  quizGenCount = 10; // Requested number of questions.
  private quizFocusOverride: string[] | null = null;
  private quizDifficultyOverride: "beginner" | "normal" | "technical" | null = null;
  /** Geçici: mini quizde farklı soru açıları için güçlü konu / çapraz etiketleri. */
  private quizChallengeOverride: string[] | null = null;
  private quizMiniMode = false;
  preparedPdfQuizPending = false;
  preparedPdfQuizKind: QuizKind = "mini_adaptive";

  quizLoading = false; // Shared loading flag for quiz actions.
  quizGenProgressMessage = "";
  private quizGenStartedAt = 0;
  private quizGenProgressTimer: number | null = null;
  quizSource: "pdf_session" | "text_summary" = "pdf_session"; // Where current quiz came from.
  quizSummaryDraft = "";
  quizSummaryLoading = false;
  quizSubmission: QuizSubmissionResponse | null = null;
  activeQuizKind: QuizKind | null = null;
  suggestions: SuggestionResponse | null = null;
  persistedResults: QuizResultsPageResponse | null = null; // Last saved backend results snapshot.
  /** Stable references for Results/Analysis templates (avoid new object every change-detection). */
  displayLastQuizSummary: LastQuizSummary | null = null;
  displayQuizSubmissionForAnalysis: QuizSubmissionResponse | null = null;
  resultsLoading = false;
  resultsLoadError: string | null = null;
  private resultsLoadGeneration = 0;
  quizHistory: QuizHistoryEntry[] = [];
  quizHistoryFilter: QuizHistoryFilter = "all";
  private readonly quizHistoryLimit = 60;
  resultsTab: "latest" | "history" = "latest";

  studyStats: StudyStats | null = null; // Global quiz + pomodoro stats.
  academicPlanForProgress: AcademicPlanView | null = null;
  academicPlanHistoryForProgress: AcademicPlanHistoryItem[] = [];
  pomodoroLiveSnapshot: PomodoroTimerSnapshot | null = null;
  statsRefreshing = false;
  statsRefreshError: string | null = null;
  pomodoroTopic: string | null = null;
  /** Incremented when routing to the PDF assistant should land on the Chat tab (Analysis / Results). */
  assistantFocusChatSeq = 0;

  constructor(
    private api: ApiService,
    private quizService: QuizService,
    private resultsService: ResultsService,
    private studyService: StudyService,
    private appFlow: AppFlowFacade,
    private currentUser: CurrentUserService,
    public i18n: I18nService,
    private pomodoroSession: PomodoroSessionService,
    private pomodoroTimer: PomodoroTimerService
  ) {}

  get isAuthenticated(): boolean { // True when session + user id exist.
    return this.currentUser.hasSession() && this.currentUser.userId != null;
  }

  get signedInId(): number {
    return this.currentUser.userId ?? 0;
  }

  /** Recreate PDF assistant when account changes so in-memory history cannot leak. */
  trackPdfAssistantUser = (_index: number, uid: number): number => uid;

  get profileNavEmailLabel(): string {
    if (!this.isAuthenticated) {
      return this.i18n.t("nav.profile");
    }
    return this.currentUser.displayName || this.i18n.t("nav.profile");
  }

  logoutFromNav(): void {
    clearQuizHistoryInStorage(this.quizHistoryStorageKey());
    this.currentUser.logout();
  }

  ngOnInit(): void {
    // Re-sync all page state when active user changes.
    this.userIdSubscription = this.currentUser.userId$.pipe(distinctUntilChanged()).subscribe((id) => {
      this.resetStudyUiForUserSwitch();
      this.pomodoroTopic = null;
      this.pomodoroSession.setTopic(null);
      if (id == null) {
        // Logged out: reset profile/lang and keep user on public tab.
        this.profile = null;
        this.badgeUnlockQueue = [];
        this.activeBadgeUnlock = null;
        this.badgeUnlockHydrated = false;
        this.clearBadgeUnlockTimer();
        this.quizHistory = [];
        this.i18n.use("en");
        if (this.protectedNav.has(this.activeNav)) {
          this.activeNav = "home";
        }
        return;
      }
      // Logged in: load profile and last saved results.
      void this.loadQuizHistory();
      this.loadProfile();
      void this.loadPersistedResults();
    });
    // Global timer refresh: keeps Progress totals updated for floating widget too.
    this.pomodoroEndedSubscription = this.pomodoroTimer.focusSegmentEnded$.subscribe(() => {
      if (!this.isAuthenticated) {
        return;
      }
      void this.refreshStats({ includeProfile: true });
    });
    this.pomodoroStateSubscription = this.pomodoroTimer.state$.subscribe((snap) => {
      this.pomodoroLiveSnapshot = snap;
    });
  }

  ngOnDestroy(): void {
    this.userIdSubscription?.unsubscribe();
    this.pomodoroEndedSubscription?.unsubscribe();
    this.pomodoroStateSubscription?.unsubscribe();
    this.clearQuizGenProgress();
    this.clearBadgeUnlockTimer();
  }

  private clearQuizGenProgress(): void { // Stop progress timer and clear message.
    if (this.quizGenProgressTimer != null) {
      clearInterval(this.quizGenProgressTimer);
      this.quizGenProgressTimer = null;
    }
    this.quizGenProgressMessage = "";
  }

  openSessionDialog(tab: "login" | "register"): void {
    // Fresh open: clear old warning before showing dialog.
    this.sessionDialogNotice = null;
    this.sessionDialogTab = tab;
    this.sessionDialogOpen = true;
  }

  onSessionDialogVisibleChange(open: boolean): void {
    this.sessionDialogOpen = open;
    if (!open) {
      this.sessionDialogNotice = null;
    }
  }

  onSessionAuthenticated(): void {
    // After login/sign-up, return to home and clear notice text.
    this.sessionDialogNotice = null;
    this.go("home", true);
  }

  onProfileAccountDeleted(): void {
    this.sessionDialogNotice = null;
    this.activeNav = "home";
    this.openSessionDialog("login");
  }

  private resetStudyUiForUserSwitch(): void { // Clear stale state after account change.
    Object.assign(this, studyUiResetPatch());
    this.syncResultsDisplay();
    this.clearBadgeUnlockTimer();
  }

  /** Rebuild cached Results/Analysis view models when submission or persisted snapshot changes. */
  private syncResultsDisplay(): void {
    this.displayLastQuizSummary = this.appFlow.buildLastQuizSummary(this.quizSubmission, this.persistedResults);
    this.displayQuizSubmissionForAnalysis = this.appFlow.buildSubmissionForAnalysis(
      this.quizSubmission,
      this.persistedResults
    );
  }

  get quizReady(): boolean {
    // "Ready" means we have at least one question to render.
    return !!(this.quizQuestions && this.quizQuestions.length > 0);
  }

  get assistantQuizFocusTopics(): string[] {
    return this.appFlow.assistantQuizFocus(this.quizSubmission, this.suggestions);
  }

  get assistantQuizChallengeTopics(): string[] {
    return this.appFlow.assistantQuizChallenge(this.quizSubmission, this.suggestions);
  }

  get assistantQuizWeakRaw(): string[] {
    return this.appFlow.assistantWeakRaw(this.quizSubmission);
  }

  get assistantQuizStrongRaw(): string[] {
    return this.appFlow.assistantStrongRaw(this.quizSubmission);
  }

  get assistantInsightTipLines(): string[] {
    return this.appFlow.assistantTipLines(this.suggestions);
  }

  get profileBadges() {
    return this.profile && this.profile.badges ? this.profile.badges : [];
  }

  get badgeSections(): BadgeSection[] {
    return buildBadgeSections(this.profileBadges);
  }

  badgeEarnedCount(): number {
    return badgeEarnedCountForSections(this.badgeSections);
  }

  badgeTotalCount(): number {
    return badgeTotalCountForSections(this.badgeSections);
  }

  badgeCompletionPercent(): number {
    return badgeCompletionPercentForSections(this.badgeSections);
  }

  badgeGroupEarnedCount(group: BadgeSection): number {
    return badgeGroupEarnedCountForSection(group);
  }

  badgeActionLine(badge: ProfileBadgeItem): string {
    const action = badgeActionForSlug(badge.slug);
    return this.i18n.lang === "tr" ? action.tr : action.en;
  }

  badgeActionCta(badge: ProfileBadgeItem): string {
    return badgeActionCtaText(badgeActionForSlug(badge.slug).target, this.i18n.lang === "tr");
  }

  runBadgeAction(badge: ProfileBadgeItem): void {
    const target = badgeActionForSlug(badge.slug).target;
    if (target === "quiz") {
      this.openQuizShellFromApp();
      return;
    }
    this.go(target);
  }

  xpBarPercent(): number {
    // XP bar loops every 100 points (0-100 visual cycle).
    const xp = this.profile ? this.profile.xp : 0;
    const cycle = 100;
    return Math.min(100, Math.round(((xp % cycle) / cycle) * 100));
  }

  go(nav: NavKey, skipAuthCheck = false, options?: GoNavOptions): void {
    const navDecision = decideGoNavigation({
      nav,
      skipAuthCheck,
      isProtectedNav: this.protectedNav.has(nav),
      isAuthenticated: this.isAuthenticated,
      options,
      quizReady: this.quizReady,
      hasQuizSubmission: !!this.quizSubmission,
      activeQuizKind: this.activeQuizKind,
    });

    if (navDecision.requiresAuthDialog) {
      this.sessionDialogNotice = this.i18n.t("auth.requiredShort");
      this.sessionDialogTab = "login";
      this.sessionDialogOpen = true;
      return;
    }

    if (navDecision.blockNavigation) {
      return;
    }

    if (navDecision.resetQuizComposer) {
      this.resetQuizComposerForManualOpen();
    }

    this.activeNav = nav;

    if (navDecision.refreshResults) {
      void this.loadPersistedResults();
      void this.loadQuizHistory();
    }

    if (navDecision.setResultsTabLatest) {
      this.resultsTab = "latest";
    }

    if (navDecision.refreshStats) {
      void this.refreshStats({ includeProfile: true });
    }
  }

  setResultsTab(tab: "latest" | "history"): void {
    this.resultsTab = tab;
  }

  navClass(nav: NavKey): string {
    return navClassFor(this.activeNav, nav);
  }

  /** Outer page shell: padding + scroll behavior (avoid nested ternaries in the template). */
  pageShellClass(): string {
    return pageShellClassFor(this.activeNav);
  }

  quizNavClass(): string {
    return quizNavClassFor({
      activeNav: this.activeNav,
      isAuthenticated: this.isAuthenticated,
      quizReady: this.quizReady,
      hasQuizSubmission: !!this.quizSubmission,
    });
  }

  loadProfile(): void {
    // Guest mode: profile panel should stay empty.
    if (!this.isAuthenticated) {
      this.profile = null;
      return;
    }
    this.api.getProfile().subscribe(
      (p) => {
        const previousProfile = this.profile;
        // Use server locale so UI language follows user profile.
        this.profile = p;
        this.i18n.use(normalizeAppLang(p.locale));
        this.queueNewBadgeUnlocks(previousProfile, p);
      },
      (err: unknown) => {
        if (isUnauthorizedHttpError(err)) {
          // Token expired/invalid -> force logout and safe UI reset.
          this.forceLogoutToPublicHome();
          return;
        }
        // Soft fallback so UI still works even if profile endpoint fails.
        this.profile = profileFallbackEn();
        this.i18n.use("en");
      }
    );
  }

  private forceLogoutToPublicHome(): void {
    this.currentUser.logout();
    this.profile = null;
    this.i18n.use("en");
    if (this.protectedNav.has(this.activeNav)) {
      this.activeNav = "home";
    }
  }

  private clearBadgeUnlockTimer(): void {
    if (this.badgeUnlockTimer != null) {
      window.clearTimeout(this.badgeUnlockTimer);
      this.badgeUnlockTimer = null;
    }
  }

  private queueNewBadgeUnlocks(previous: ProfileResponse | null, current: ProfileResponse): void {
    const next = resolveNewBadgeUnlocks({
      previous,
      current,
      hydrated: this.badgeUnlockHydrated,
    });
    this.badgeUnlockHydrated = next.nextHydrated;
    if (!next.newBadges.length) {
      return;
    }
    this.badgeUnlockQueue.push(...next.newBadges);
    this.showNextBadgeUnlock();
  }

  private showNextBadgeUnlock(): void {
    if (this.activeBadgeUnlock || !this.badgeUnlockQueue.length) {
      return;
    }
    this.activeBadgeUnlock = this.badgeUnlockQueue.shift() || null;
    this.clearBadgeUnlockTimer();
    this.badgeUnlockTimer = window.setTimeout(() => {
      this.activeBadgeUnlock = null;
      this.badgeUnlockTimer = null;
      this.showNextBadgeUnlock();
    }, 4200);
  }

  onAssistantQuiz(payload: QuizReadyPayload | null): void {
    // Assistant can replace the current quiz in one shot.
    if (!payload || !payload.questions?.length) {
      this.quizQuestions = null;
      this.quizTimeLimitSeconds = 0;
      this.activeQuizKind = null;
    } else {
      this.quizSource = "pdf_session";
      this.quizQuestions = payload.questions;
      this.activeQuizKind = "standard";
      this.quizTimeLimitSeconds =
        typeof payload.time_limit_seconds === "number" && payload.time_limit_seconds > 0
          ? payload.time_limit_seconds
          : 0;
    }
    this.quizLoading = false;
    this.preparedPdfQuizPending = false;
    this.quizSubmission = null;
    this.studyStats = null;
    this.uploadError = null;
    this.loadProfile();
  }

  onPdfSession(ev: { documentId: string | null; filename: string | null }): void {
    // New document session means old suggestions are no longer reliable.
    const next = normalizeDocumentId(ev.documentId);
    const prev = normalizeDocumentId(this.activePdfDocumentId);
    if (prev !== next) {
      this.suggestions = null;
      this.preparedPdfQuizPending = false;
      if (this.isAuthenticated && next) {
        // PDF upload can award badges; refresh profile so unlock toast appears immediately.
        this.loadProfile();
      }
    }
    this.activePdfDocumentId = next;
  }

  get activePdfDocumentIdForQuiz(): string | null {
    return normalizeDocumentId(this.activePdfDocumentId);
  }

  get canGenerateQuizFromPdf(): boolean {
    return this.activePdfDocumentIdForQuiz != null;
  }

  get canRunPdfBasedQuiz(): boolean {
    return !!(this.activePdfDocumentIdForQuiz || normalizeDocumentId(this.persistedResults?.document_id ?? null));
  }

  async generateQuizFromPdf(): Promise<void> {
    // This path generates a quiz from the active PDF session.
    const docId = this.activePdfDocumentIdForQuiz;
    if (!docId) {
      logQuizDocumentFlow("app generate quiz blocked", { reason: "no_document_id" });
      this.uploadError = this.i18n.t("quiz.noActiveSession");
      return;
    }
    if (this.quizLoading) {
      return;
    }
    this.preparedPdfQuizPending = false;
    this.quizSubmission = null;
    this.quizQuestions = null;
    this.quizTimeLimitSeconds = 0;
    this.activeQuizKind = null;
    this.quizSource = "pdf_session";
    logQuizDocumentFlow("app generate quiz start", {
      active_document_id: docId,
      active_document_id_prefix: docId.slice(0, 16),
      user_id: this.signedInId,
    });
    const maxN = this.quizMiniMode ? MAX_MINI_QUIZ_QUESTIONS : MAX_STANDARD_QUIZ_QUESTIONS;
    const n = Math.max(1, Math.min(maxN, Math.round(this.quizGenCount)));
    const quizKind = this.quizMiniMode ? "mini_adaptive" : "standard";
    this.quizMiniMode = false;
    const override = this.quizFocusOverride;
    this.quizFocusOverride = null;
    const focus = override && override.length ? override.slice(0, 10) : this.assistantQuizFocusTopics.slice(0, 8);
    const chOverride = this.quizChallengeOverride;
    this.quizChallengeOverride = null;
    const challenge = chOverride && chOverride.length ? chOverride : this.assistantQuizChallengeTopics;
    const difficulty = this.quizDifficultyOverride ?? "normal";
    this.quizDifficultyOverride = null;
    this.quizLoading = true;
    this.uploadError = null;
    this.quizGenStartedAt = Date.now();
    const phaseLang = this.i18n.lang === "tr" ? "tr" : "en";
    this.quizGenProgressMessage = quizGenerationPhaseMessage(this.quizGenStartedAt, phaseLang);
    this.quizGenProgressTimer = window.setInterval(() => {
      // Show a simple progress sentence while backend is working.
      this.quizGenProgressMessage = quizGenerationPhaseMessage(this.quizGenStartedAt, phaseLang);
    }, 450);
    try {
      logQuizDocumentFlow("app generate quiz client payload", {
        document_id: docId,
        user_id: this.signedInId,
        num_questions: n,
        max_topics: 6,
        focus_topics: focus,
        challenge_topics: challenge,
        locale: this.i18n.lang,
        difficulty,
        quiz_kind: quizKind,
      });
      const result = await this.quizService.runPdfQuiz({
        documentId: docId,
        userId: this.signedInId,
        numQuestions: n,
        maxTopics: 6,
        focusTopics: focus,
        challengeTopics: challenge.length ? challenge : undefined,
        locale: this.i18n.lang,
        difficulty,
        quizKind,
      });
      if ("reason" in result) {
        // If backend says document is gone, clear local session link.
        if (result.reason === "document_gone") {
          stripDocumentFromLocalHistory(this.signedInId, docId, this.currentUser.email);
          this.activePdfDocumentId = null;
          this.uploadError = this.i18n.t("quiz.pdfSessionMaybeExpired");
        } else {
          this.uploadError = result.message;
        }
        return;
      }
      this.quizQuestions = result.questions;
      this.quizTimeLimitSeconds = result.timeLimitSeconds;
      this.activeQuizKind = quizKind;
      this.quizSubmission = null;
      this.suggestions = null;
      this.loadProfile();
    } finally {
      // Always stop loading indicators, even on early return paths.
      this.quizLoading = false;
      this.clearQuizGenProgress();
    }
  }

  async generateQuizFromSummaryText(): Promise<void> {
    // This path creates a quiz from free text notes (no PDF needed).
    if (this.quizLoading || this.quizSummaryLoading) {
      return;
    }
    this.quizSummaryLoading = true;
    this.quizLoading = true;
    this.preparedPdfQuizPending = false;
    this.uploadError = null;
    this.quizSubmission = null;
    this.quizQuestions = null;
    this.quizTimeLimitSeconds = 0;
    this.activeQuizKind = null;
    this.quizSource = "text_summary";
    const maxN = MAX_STANDARD_QUIZ_QUESTIONS;
    const n = Math.max(1, Math.min(maxN, Math.round(this.quizGenCount)));
    const result = await this.quizService.runSummaryQuiz({
      draft: this.quizSummaryDraft,
      numQuestions: n,
      locale: this.i18n.lang,
    });
    // Service returns either "message" (error) or normalized quiz payload.
    if ("message" in result) {
      this.uploadError = result.message;
    } else {
      this.quizQuestions = result.questions;
      this.quizTimeLimitSeconds = result.timeLimitSeconds;
      this.activeQuizKind = "standard";
    }
    this.quizSummaryLoading = false;
    this.quizLoading = false;
  }

  async onSubmitQuiz(payload: QuizSubmitPayload) {
    // Submit uses the currently visible question list.
    if (!this.quizQuestions || this.quizQuestions.length === 0 || this.quizLoading) {
      return;
    }
    const n = this.quizQuestions.length;
    let answers = (payload.selectedAnswers || []).map((a) => String(a ?? "").trim());
    if (answers.length < n) {
      answers = [...answers, ...Array(n - answers.length).fill("")];
    } else if (answers.length > n) {
      answers = answers.slice(0, n);
    }

    this.quizLoading = true;
    this.uploadError = null;
    this.quizSubmission = null;
    this.suggestions = null;
    this.studyStats = null;

    const normalizedQuestions = this.appFlow.normalizeQuizQuestions(this.quizQuestions);
    const docForSubmit = this.quizSource === "pdf_session" ? this.activePdfDocumentIdForQuiz || undefined : undefined;
    const submitBody: QuizSubmissionRequest = {
      questions: normalizedQuestions,
      selected_answers: answers,
      user_id: this.signedInId,
      locale: this.i18n.lang,
      document_id: docForSubmit,
      duration_seconds: Math.max(0, Math.round(payload.durationSeconds || 0)),
      quiz_source: this.quizSource,
    };

    try {
      // Submit + suggestions are combined in one service call.
      const submissionResult = await this.quizService.runSubmitQuiz({
        payload: submitBody,
        locale: this.i18n.lang,
      });
      if ("message" in submissionResult) {
        this.uploadError = submissionResult.message;
        return;
      }
      this.quizSubmission = submissionResult.submission;
      this.suggestions = submissionResult.suggestions;
      this.syncResultsDisplay();
      void this.loadQuizHistory();
      void this.quizService.fetchSuggestionsSafe(submitBody).then((s) => {
        if (s) {
          this.suggestions = s;
        }
      });
      if (!environment.production) {
        console.log("[StudyFlow results] quiz submit normalized", {
          score: this.quizSubmission.score_percentage,
          correct: this.quizSubmission.total_correct,
          wrong: this.quizSubmission.total_wrong,
          topicRows: this.quizSubmission.topic_analysis?.topics?.length ?? 0,
          questionFeedback: this.quizSubmission.question_feedback?.length ?? 0,
        });
      }

      // Open Results immediately so a dev-only log typo or slow refresh never skips navigation.
      this.go("results", true);
      // Stop "Submitting…" before stats/profile refresh — those calls can be slow and are not needed to show results.
      this.quizLoading = false;
      // `go("results")` already starts `loadPersistedResults()`; avoid a duplicate awaited fetch here.
      void this.refreshStats({ includeProfile: true });
    } finally {
      this.quizLoading = false;
    }
  }

  async refreshStats(options?: { includeProfile?: boolean; manual?: boolean }) {
    // Lightweight refresh used when entering Stats tab.
    if (!this.isAuthenticated) {
      this.studyStats = null;
      this.academicPlanForProgress = null;
      this.academicPlanHistoryForProgress = [];
      this.statsRefreshError = null;
      return;
    }
    if (this.statsRefreshing) {
      return;
    }
    const includeProfile = !!options?.includeProfile;
    const manual = !!options?.manual;
    if (manual) {
      this.statsRefreshError = null;
    }
    this.statsRefreshing = true;
    try {
      this.studyStats = await this.studyService.getStats();
      this.academicPlanForProgress = await this.fetchLatestAcademicPlanForProgress();
      this.academicPlanHistoryForProgress = await this.fetchAcademicPlanHistoryForProgress();
      if (includeProfile) {
        this.loadProfile();
      }
    } catch (err: unknown) {
      if (isUnauthorizedHttpError(err)) {
        this.studyStats = null;
        this.academicPlanForProgress = null;
        this.academicPlanHistoryForProgress = [];
        this.statsRefreshError = null;
        this.forceLogoutToPublicHome();
        return;
      }
      this.studyStats = null;
      this.academicPlanForProgress = null;
      this.academicPlanHistoryForProgress = [];
      if (manual) {
        this.statsRefreshError = readApiError(
          err,
          this.i18n.lang === "tr"
            ? "İlerleme verileri yenilenemedi. Lütfen tekrar deneyin."
            : "Could not refresh progress data. Please try again."
        );
      }
    } finally {
      this.statsRefreshing = false;
    }
  }

  private async fetchLatestAcademicPlanForProgress(): Promise<AcademicPlanView | null> {
    const today = this.clientTodayIso();
    const res = await new Promise<{ has_plan: boolean; plan: AcademicPlanView | null }>((resolve, reject) => {
      this.api.getAcademicPlanLatest(today).subscribe({
        next: (v) => resolve(v),
        error: (e: unknown) => reject(e),
      });
    });
    return res?.has_plan && res.plan ? res.plan : null;
  }

  private async fetchAcademicPlanHistoryForProgress(): Promise<AcademicPlanHistoryItem[]> {
    const res = await new Promise<{ items: AcademicPlanHistoryItem[] }>((resolve, reject) => {
      this.api.getAcademicPlanHistory().subscribe({
        next: (v) => resolve(v),
        error: (e: unknown) => reject(e),
      });
    });
    return Array.isArray(res?.items) ? res.items : [];
  }

  private clientTodayIso(): string {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  onManualStatsRefresh(): void {
    void this.refreshStats({ includeProfile: true, manual: true });
  }

  /** Human-readable duration for Progress (Pomodoro totals). */
  formatProgressDurationSeconds(total: number): string {
    return formatProgressDurationSecondsText(total, this.i18n.lang === "tr");
  }

  progressFocusSeconds(st: StudyStats | null | undefined): number {
    return progressFocusSecondsValue(st, this.pomodoroLiveSnapshot);
  }

  hasLiveFocusIncrement(): boolean {
    return hasLiveFocusIncrementValue(this.pomodoroLiveSnapshot);
  }

  pomodoroTopicBreakdown(): { topic: string; seconds: number; pct: number }[] {
    return pomodoroTopicBreakdownRows(this.studyStats);
  }

  quizLifecycleBar(): { kind: "correct" | "wrong" | "blank"; pct: number }[] {
    return quizLifecycleBarRows(this.studyStats);
  }

  statusProgressCounts(): { completed: number; pending: number; missed: number; total: number } {
    const plan = this.academicPlanForProgress;
    const history = this.academicPlanHistoryForProgress;
    let completed = 0;
    let pending = 0;
    let missed = 0;
    let total = 0;

    if (plan) {
      const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
      if (tasks.length) {
        completed += tasks.filter((task) => task.status === "completed").length;
        pending += tasks.filter((task) => task.status === "pending").length;
        missed += tasks.filter((task) => task.status === "missed").length;
        total += tasks.length;
      } else {
        const planCompleted = Math.max(0, Number(plan.completed_count || 0));
        const planMissed = Math.max(0, Number(plan.missed_count || 0));
        const planTotal = Math.max(0, Number(plan.total_task_count || planCompleted + planMissed));
        const planPending = Math.max(0, planTotal - planCompleted - planMissed);
        completed += planCompleted;
        pending += planPending;
        missed += planMissed;
        total += planTotal;
      }
    }

    if (Array.isArray(history) && history.length) {
      for (const item of history) {
        const itemTotal = Math.max(0, Number(item.total_task_count || 0));
        const itemCompleted = Math.max(0, Number(item.completed_count || 0));
        const itemMissed = Math.max(0, Number(item.missed_count || 0));
        completed += itemCompleted;
        missed += itemMissed;
        total += itemTotal;
      }
    }

    if (total > 0) {
      return { completed, pending, missed, total };
    }

    const st = this.studyStats;
    if (!st) {
      return { completed: 0, pending: 0, missed: 0, total: 0 };
    }
    const quizCompleted = Math.max(0, Number(st.quiz_sum_correct || 0));
    const quizPending = Math.max(0, Number(st.quiz_sum_unanswered || 0));
    const quizMissed = Math.max(0, Number(st.quiz_sum_wrong || 0));
    const quizTotal = Math.max(0, Number(st.quiz_sum_question_slots || quizCompleted + quizPending + quizMissed));
    return { completed: quizCompleted, pending: quizPending, missed: quizMissed, total: quizTotal };
  }

  statusProgressPercent(): number {
    const c = this.statusProgressCounts();
    if (!c.total) {
      return 0;
    }
    return Math.max(0, Math.min(100, Math.round((c.completed / c.total) * 100)));
  }

  statusProgressMessage(): string {
    const c = this.statusProgressCounts();
    if (!c.total) {
      return this.i18n.lang === "tr" ? "Henüz yeterli veri yok." : "No progress data yet.";
    }
    if (c.missed > c.completed) {
      return this.i18n.lang === "tr"
        ? "Dengeni kaçırıyorsun, yanlışları azaltmak için kısa tekrar ekle."
        : "You're falling behind, add short review blocks to reduce mistakes.";
    }
    if (c.completed >= Math.max(6, c.missed * 2)) {
      return this.i18n.lang === "tr" ? "Harika gidiyorsun, bu tempoyu koru." : "Great momentum, keep this pace.";
    }
    return this.i18n.lang === "tr" ? "Planlı şekilde ilerliyorsun." : "You're on track.";
  }

  statusProgressToneClass(): string {
    const c = this.statusProgressCounts();
    if (c.total === 0) {
      return "sf-statusProgressNote sf-statusProgressNote--neutral";
    }
    if (c.missed > c.completed) {
      return "sf-statusProgressNote sf-statusProgressNote--warn";
    }
    if (this.statusProgressPercent() >= 70) {
      return "sf-statusProgressNote sf-statusProgressNote--good";
    }
    return "sf-statusProgressNote sf-statusProgressNote--neutral";
  }

  async loadPersistedResults(): Promise<void> {
    // Pull latest saved result snapshot for Results/Analysis pages.
    const blockUi = !this.quizSubmission;
    const generation = ++this.resultsLoadGeneration;
    this.resultsLoadError = null;
    this.resultsLoading = true;
    try {
      this.persistedResults = await this.resultsService.getUserResults(this.i18n.lang);
      if (!this.activePdfDocumentIdForQuiz) {
        const fallbackDoc = normalizeDocumentId(this.persistedResults?.document_id ?? null);
        if (fallbackDoc) {
          this.activePdfDocumentId = fallbackDoc;
        }
      }
    } catch (e: unknown) {
      this.resultsLoadError = readApiError(e, this.i18n.lang === "tr"
        ? "Sonuçlar yüklenemedi. Lütfen tekrar deneyin."
        : "Could not load results. Please try again.");
      if (blockUi) {
        // No in-memory fallback, so clear stale persisted payload.
        this.persistedResults = null;
      }
    } finally {
      this.syncResultsDisplay();
      if (generation === this.resultsLoadGeneration) {
        this.resultsLoading = false;
      }
    }
  }

  onStartPomodoro(topic: string | null) {
    // Open floating timer widget prefilled with selected topic.
    this.pomodoroTopic = topic;
    this.pomodoroSession.setTopic(topic);
    this.pomodoroSession.expand();
  }

  onPomodoroEnded() {
    // Small refresh after timer ends: stats + profile XP/badges.
    void this.refreshStats({ includeProfile: true });
  }

  startFlow() {
    this.go("pdf");
  }

  goQuizFromHero() {
    if (this.quizReady) {
      this.go("quiz", false, { bypassQuizNavLock: true });
    } else {
      this.go("pdf");
    }
  }

  /** Opens the quiz area from assistant, home cards, etc. Header Quiz is display-only for signed-in users. */
  openQuizShellFromApp(): void {
    this.go("quiz", false, { bypassQuizNavLock: true });
  }

  /** Opens PDF assistant with the center panel on Chat (e.g. from Analysis). */
  openAssistantToChat(): void {
    this.assistantFocusChatSeq++;
    this.go("pdf");
  }

  get topicAnalysisForResults(): TopicAnalysisResponse | null {
    return (
      this.displayQuizSubmissionForAnalysis?.topic_analysis ??
      this.quizSubmission?.topic_analysis ??
      this.persistedResults?.topic_analysis ??
      null
    );
  }

  get suggestionsForResults(): SuggestionResponse | null {
    if (this.suggestions) {
      return this.suggestions;
    }
    return this.persistedResults?.suggestions ?? null;
  }

  get lifetimeTopicAnalysisForResults(): TopicAnalysisResponse | null {
    return this.persistedResults?.lifetime_topic_analysis ?? null;
  }

  get topicProgressForResults(): TopicProgressItem[] {
    return this.persistedResults?.topic_progress ?? [];
  }

  onWeakTopicQuizFromResults(): void {
    void this.startMicroWeakQuiz(this.quizGenCount);
  }

  private hasPdfSessionForQuiz(): boolean {
    const docId = resolveActiveQuizDocumentId(this.activePdfDocumentId, this.persistedResults?.document_id ?? null);
    if (!docId) {
      this.uploadError = this.i18n.t("quiz.uploadPdfFirst");
      return false;
    }
    this.activePdfDocumentId = docId;
    return true;
  }

  private async runMiniQuizFlow(): Promise<void> {
    // Shared "open quiz tab + generate now" behavior.
    const wasMini = this.quizMiniMode;
    this.quizSubmission = null;
    this.go("quiz", false, { allowQuizSession: true, bypassQuizNavLock: true });
    this.uploadError = null;
    await this.generateQuizFromPdf();
    if (!this.quizQuestions?.length) {
      this.uploadError =
        this.uploadError ||
        (wasMini
          ? this.i18n.t("quiz.miniQuizFailed")
          : this.i18n.lang === "tr"
          ? "Quiz başlatılamadı. Lütfen tekrar deneyin."
          : "Quiz could not be started. Please try again.");
    }
  }

  async startPreparedPdfQuiz(): Promise<void> {
    if (!this.preparedPdfQuizPending || this.quizLoading || this.quizSummaryLoading) {
      return;
    }
    this.preparedPdfQuizPending = false;
    await this.runMiniQuizFlow();
  }

  get filteredQuizHistory(): QuizHistoryEntry[] {
    const list = this.quizHistory;
    if (this.quizHistoryFilter === "all") {
      return list;
    }
    if (this.quizHistoryFilter === "mini") {
      return list.filter((x) => x.quizKind === "mini_adaptive");
    }
    return list.filter((x) => x.quizKind === "standard");
  }

  get normalQuizHistory(): QuizHistoryEntry[] {
    return this.quizHistory.filter((x) => x.quizKind === "standard");
  }

  get miniOnlyQuizHistory(): QuizHistoryEntry[] {
    return this.quizHistory.filter((x) => x.quizKind === "mini_adaptive");
  }

  get showQuizHistoryPanel(): boolean {
    // Hide history while user is actively solving the current quiz.
    return !this.quizReady || !!this.quizSubmission;
  }

  quizKindLabel(kind: QuizKind): string {
    return quizKindLabelText(kind, this.i18n.lang === "tr");
  }

  quizSourceLabel(source: QuizHistoryEntry["quizSource"]): string {
    return quizSourceLabelText(source, this.i18n.lang === "tr");
  }

  quizShortComment(score: number): string {
    return quizShortCommentText(score, this.i18n.lang === "tr");
  }

  async retryMiniFromHistory(entry: QuizHistoryEntry): Promise<void> {
    if (!this.hasPdfSessionForQuiz()) {
      this.go("pdf");
      return;
    }
    const request = retryMiniFromHistoryRequest({
      manualQuizGenCount: this.quizGenCount,
      recommendedMiniCount: entry.recommendedMiniCount,
      focusTopics: entry.focusTopics,
      suggestedMiniQuizTopic: entry.suggestedMiniQuizTopic,
      maxMiniQuizQuestions: MAX_MINI_QUIZ_QUESTIONS,
    });
    await this.onTailoredMiniQuiz(request);
  }

  private resetQuizComposerForManualOpen(): void {
    Object.assign(this, quizComposerResetPatch());
    this.clearQuizGenProgress();
  }

  private quizHistoryStorageKey(): string | null {
    if (!this.isAuthenticated) {
      return null;
    }
    clearLegacyQuizHistoryStorage(this.signedInId);
    return buildQuizHistoryStorageKey(this.signedInId, this.currentUser.email);
  }

  private async loadQuizHistory(): Promise<void> {
    if (!this.isAuthenticated) {
      this.quizHistory = [];
      return;
    }
    const key = this.quizHistoryStorageKey();
    try {
      const res = await this.studyService.getQuizHistory(this.quizHistoryLimit);
      // Signed-in users: server is the only source of truth (never merge stale local rows).
      this.quizHistory = mapServerQuizHistoryItems(res.items || []).slice(0, this.quizHistoryLimit);
      if (key) {
        saveQuizHistoryToStorage(key, this.quizHistory, this.quizHistoryLimit);
      }
    } catch {
      const local = loadQuizHistoryFromStorage(key, this.quizHistoryLimit, MAX_MINI_QUIZ_QUESTIONS);
      this.quizHistory = local;
    }
  }

  private saveQuizHistory(): void {
    saveQuizHistoryToStorage(this.quizHistoryStorageKey(), this.quizHistory, this.quizHistoryLimit);
  }

  clearQuizHistory(): void {
    this.quizHistory = [];
    this.quizHistoryFilter = "all";
    clearQuizHistoryInStorage(this.quizHistoryStorageKey());
  }

  private async launchPdfQuiz(options: {
    count: number;
    miniMode: boolean;
    focusTopics?: string[] | null;
    difficulty?: "beginner" | "normal" | "technical" | null;
    refreshResultsFirst?: boolean;
  }): Promise<void> {
    if (!this.hasPdfSessionForQuiz()) {
      return;
    }
    this.quizMiniMode = options.miniMode;
    this.preparedPdfQuizKind = options.miniMode ? "mini_adaptive" : "standard";
    this.quizGenCount = options.count;
    this.quizFocusOverride = options.focusTopics?.length ? options.focusTopics : null;
    this.quizDifficultyOverride = options.difficulty ?? null;
    if (options.refreshResultsFirst) {
      // Refresh snapshot in background so UI can switch to Quiz immediately.
      void this.loadPersistedResults().catch(() => {
        // Keep in-memory data on refresh failure.
      });
    }
    this.preparedPdfQuizPending = true;
    this.quizQuestions = null;
    this.quizTimeLimitSeconds = 0;
    this.quizSubmission = null;
    this.activeQuizKind = null;
    this.uploadError = null;
    this.go("quiz", false, { allowQuizSession: true, bypassQuizNavLock: true });
  }

  async startMicroWeakQuiz(n = 3): Promise<void> {
    // Quick retry focused on weakest topic from latest attempt.
    const focus = this.appFlow.miniQuizFocusTopics(this.quizSubmission, this.displayQuizSubmissionForAnalysis);
    await this.launchPdfQuiz({
      count: resolveMiniQuizCount(this.quizGenCount, n, MAX_MINI_QUIZ_QUESTIONS),
      miniMode: true,
      focusTopics: focus,
      refreshResultsFirst: true,
    });
  }

  async onTailoredMiniQuiz(ev: TailoredMiniQuizPayload): Promise<void> {
    const sub = this.quizSubmission ?? this.displayQuizSubmissionForAnalysis;
    const cfg = resolveTailoredMiniQuizConfig({
      event: ev,
      manualQuizGenCount: this.quizGenCount,
      maxMiniQuizQuestions: MAX_MINI_QUIZ_QUESTIONS,
      submissionForScoring: sub,
      strongTopics: this.appFlow.assistantStrongRaw(sub),
    });
    this.quizChallengeOverride = cfg.challengeTopics;
    await this.launchPdfQuiz({
      count: cfg.count,
      miniMode: true,
      focusTopics: cfg.focusTopics,
      difficulty: cfg.difficulty,
    });
  }

  async startCoachingSuggestedQuiz(): Promise<void> {
    // Uses backend coaching pack to pick size, focus, and difficulty.
    const pack = this.suggestionsForResults?.coaching_pack;
    const sq = pack?.suggested_quiz;
    if (!sq) {
      await this.startMicroWeakQuiz(this.quizGenCount);
      return;
    }
    const cfg = resolveCoachingSuggestedQuizConfig({
      numQuestions: sq.num_questions,
      difficulty: sq.difficulty,
      focusTopics: sq.focus_topics,
      maxStandardQuizQuestions: MAX_STANDARD_QUIZ_QUESTIONS,
    });
    await this.launchPdfQuiz({
      count: cfg.count,
      miniMode: false,
      focusTopics: cfg.focusTopics,
      difficulty: cfg.difficulty,
    });
  }

  async startMistakesRetryQuiz(): Promise<void> {
    // Build a mini quiz from topics with wrong answers.
    const sub = this.quizSubmission ?? this.displayQuizSubmissionForAnalysis;
    const wrongTopics = this.appFlow.mistakeTopics(sub);
    if (!wrongTopics.length) {
      return;
    }
    await this.launchPdfQuiz({
      count: resolveMistakesRetryCount(this.quizGenCount, wrongTopics.length, MAX_MINI_QUIZ_QUESTIONS),
      miniMode: true,
      focusTopics: wrongTopics,
    });
  }

  startEasyRetryQuiz(): void {
    void this.startMicroWeakQuiz(3);
  }
}
