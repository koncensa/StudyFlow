// cmp: pdf-assistant | tr: pdf asistan — yükleme, özet, sohbet, quiz / en: pdf assistant upload summary chat quiz

import {
  AfterViewInit,
  ChangeDetectorRef,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  OnInit,
  Output,
  SimpleChanges,
  ViewChild,
} from "@angular/core";
import { Subscription } from "rxjs";

import { AppLang } from "../../i18n/app-lang";
import { I18nService } from "../../i18n/i18n.service";
import { ApiService } from "../../services/api.service";
import { ExplainLevel, QuizReadyPayload, StudyChatMode } from "../../models/types";
import { logQuizDocumentFlow, normalizeDocumentId } from "../../utils/document-id";
import {
  AssistantMessage,
  CenterTab,
  PdfSessionSnapshot,
  SessionMode,
  buildPersonalizationOpts,
  generateLocalSessionKey,
  isValidStudyflowUserId,
  loadPdfSessionHistory,
  mapServerPdfSessionItems,
  mergePdfSessionHistories,
  parseStoredSessionMode,
  pdfSessionScope,
  PdfSessionStorageScope,
  persistActiveSelection,
  restoreActiveSelectionKey,
  savePdfSessionHistory,
  stripLegacyModePreviewMessages,
  upsertActivePdfSnapshot,
} from "./pdf-assistant.state";
import { CurrentUserService } from "../../session/current-user.service";
import {
  SummaryFormatPref,
  SummaryLengthPref,
  SummaryStylePref,
  displaySessionFilenameValue,
  insightLine,
  looksLikeExpiredPdfSessionReply,
  quizMaxTopicsForLevel,
  shortTip,
  summaryPrefsForMode,
} from "./pdf-assistant.utils";
import {
  applyStudySettingsAction,
  applyStudySettingsIfNeededAction,
  dropServerDocumentFromStateAction,
  generateQuizFromSidebarAction,
  newDocumentAction,
  onComposerKeydownAction,
  onFileChangeAction,
  onShellDragLeaveAction,
  onShellDragOverAction,
  onShellDropAction,
  removeSessionAction,
  runStarterActionAction,
  runUploadAction,
  selectSessionAction,
  sendMessageAction,
  showSettingsHintAction,
  uploadFileAction,
  verifyBackendSessionAfterSelectAction,
} from "./pdf-assistant.actions";

const MAX_QUIZ_QUESTIONS = 15;

@Component({
  selector: "app-pdf-assistant",
  templateUrl: "./pdf-assistant.component.html",
  styleUrls: ["./pdf-assistant.component.css", "./pdf-assistant.modal.css"],
})
export class PdfAssistantComponent implements OnInit, OnChanges, OnDestroy, AfterViewInit {
  @ViewChild("threadRef") threadRef?: ElementRef<HTMLDivElement>;
  @ViewChild("threadContentRef") threadContentRef?: ElementRef<HTMLDivElement>;
  @Input() userId: number | null = null;
  @Input() locale: AppLang = "en";
  @Input() quizFocusTopics: string[] = [];
  @Input() quizChallengeTopics: string[] = [];
  @Input() weakTopicsFromQuiz: string[] = [];
  @Input() strongTopicsFromQuiz: string[] = [];
  @Input() insightTipLines: string[] = [];
  @Input() focusAssistantChatSeq = 0;

  @Output() quizReady = new EventEmitter<QuizReadyPayload | null>();
  @Output() sessionChange = new EventEmitter<{ documentId: string | null; filename: string | null }>();
  @Output() openQuizTab = new EventEmitter<void>();

  tutorMessages: AssistantMessage[] = [];
  summaryOutput = "";
  explainOutput = "";
  examOutput = "";
  activeCenterTab: CenterTab = "chat";
  documentId: string | null = null;
  uploadedName: string | null = null;
  activeSessionKey: string | null = null;
  history: PdfSessionSnapshot[] = [];

  uploadLoading = false;
  chatLoading = false;
  quizGenState: "idle" | "loading" | "success" | "error" = "idle";
  applyDemoLoading = false;
  error: string | null = null;
  draft = "";

  numQuestions = 15;
  dragOver = false;
  quizModalOpen = false;

  sessionMode: SessionMode = "tutor_chat";
  explainLevel: ExplainLevel = "normal";
  draftSessionMode: SessionMode = "tutor_chat";
  settingsAppliedMessage: string | null = null;
  private settingsFlashTimer: ReturnType<typeof setTimeout> | null = null;

  private stickThreadToBottom = true;
  private programmaticThreadScroll = false;
  private readonly nearBottomThresholdPx = 100;
  private chatRequestSub: Subscription | null = null;
  private threadContentResizeObs: { disconnect(): void; observe(target: Element): void } | null = null;
  private historyVerifySeq = 0;
  historySessionCheckPending = false;
  readonly modeOptions: { id: SessionMode; label: string }[] = [
    { id: "tutor_chat", label: "Chat" },
    { id: "quick_summary", label: "Quick summary" },
    { id: "explain_simple", label: "Explain simply" },
    { id: "exam_focus", label: "Exam focus" },
    { id: "quiz_mode", label: "Quiz mode" },
  ];

  constructor(
    private api: ApiService,
    private cdr: ChangeDetectorRef,
    private currentUser: CurrentUserService,
    public i18n: I18nService
  ) {}

  private storageScope(): PdfSessionStorageScope | null {
    return pdfSessionScope(this.userId, this.currentUser.email);
  }

  get chatStudyMode(): StudyChatMode {
    if (this.sessionMode === "quiz_mode") {
      return "quiz_coach";
    }
    return this.sessionMode as StudyChatMode;
  }

  ngOnInit(): void {
    void this.reloadHistoryForUser();
  }

  get activeDocumentIdForApi(): string | null {
    return normalizeDocumentId(this.documentId);
  }

  get hasServerPdf(): boolean {
    return this.activeDocumentIdForApi != null;
  }

  get hasLocalSession(): boolean {
    return this.activeSessionKey != null || this.hasServerPdf;
  }

  get hasSession(): boolean {
    return this.hasLocalSession;
  }

  get showStaleSessionHint(): boolean {
    if (!this.hasLocalSession || this.hasServerPdf || this.historySessionCheckPending) {
      return false;
    }
    return !this.error;
  }

  get showSessionCheckingHint(): boolean {
    return this.historySessionCheckPending && this.hasLocalSession && !this.hasServerPdf;
  }

  get canAcceptPdfDrop(): boolean {
    return !this.uploadLoading && !this.chatLoading;
  }

  get canGenerateQuiz(): boolean {
    return this.hasServerPdf && this.quizGenState !== "loading" && !this.chatLoading;
  }

  ngAfterViewInit(): void {
    this.scheduleThreadObserverAttach();
  }

  private scheduleThreadObserverAttach(): void {
    setTimeout(() => this.attachThreadContentResizeObserver(), 0);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes["userId"]) {
      if (!changes["userId"].firstChange) {
        this.resetForUserSwitch();
      }
      void this.reloadHistoryForUser();
    }
    const focusCh = changes["focusAssistantChatSeq"];
    if (focusCh && typeof focusCh.currentValue === "number") {
      const cur = focusCh.currentValue;
      const shouldApply =
        (!focusCh.firstChange && cur !== focusCh.previousValue) || (focusCh.firstChange && cur > 0);
      if (shouldApply) {
        this.applyExternalChatTabFocus();
      }
    }
  }

  private applyExternalChatTabFocus(): void {
    if (this.sessionMode === "quiz_mode") {
      this.sessionMode = "tutor_chat";
      this.draftSessionMode = "tutor_chat";
    }
    this.setCenterTab("chat");
    this.cdr.markForCheck();
  }

  ngOnDestroy(): void {
    this.threadContentResizeObs?.disconnect();
    this.threadContentResizeObs = null;
    this.clearSettingsFlash();
    this.cancelChat();
  }

  private attachThreadContentResizeObserver(): void {
    const RO = (globalThis as unknown as { ResizeObserver?: new (cb: () => void) => { observe(el: Element): void; disconnect(): void } })
      .ResizeObserver;
    if (!RO) {
      return;
    }
    const inner = this.threadContentRef?.nativeElement;
    if (!inner) {
      this.threadContentResizeObs?.disconnect();
      this.threadContentResizeObs = null;
      return;
    }
    this.threadContentResizeObs?.disconnect();
    this.threadContentResizeObs = new RO(() => {
      if (!this.stickThreadToBottom) {
        return;
      }
      const pane = this.threadRef?.nativeElement;
      if (!pane) {
        return;
      }
      this.programmaticThreadScroll = true;
      pane.scrollTop = pane.scrollHeight;
      requestAnimationFrame(() => {
        pane.scrollTop = pane.scrollHeight;
        setTimeout(() => {
          this.programmaticThreadScroll = false;
        }, 50);
      });
    });
    this.threadContentResizeObs.observe(inner);
  }

  private isNearThreadBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight < this.nearBottomThresholdPx;
  }

  onThreadScroll(): void {
    if (this.programmaticThreadScroll) {
      return;
    }
    const el = this.threadRef?.nativeElement;
    if (!el) {
      return;
    }
    this.stickThreadToBottom = this.isNearThreadBottom(el);
  }

  private scrollThreadToBottom(force: boolean): void {
    this.cdr.detectChanges();
    const apply = (): void => {
      const el = this.threadRef?.nativeElement;
      if (!el) {
        return;
      }
      if (!force && !this.stickThreadToBottom) {
        return;
      }
      this.programmaticThreadScroll = true;
      el.scrollTop = el.scrollHeight;
      requestAnimationFrame(() => {
        const t = this.threadRef?.nativeElement;
        if (t) {
          t.scrollTop = t.scrollHeight;
        }
        requestAnimationFrame(() => {
          const t2 = this.threadRef?.nativeElement;
          if (t2) {
            t2.scrollTop = t2.scrollHeight;
          }
          setTimeout(() => {
            this.programmaticThreadScroll = false;
          }, 40);
        });
      });
    };
    apply();
    setTimeout(apply, 0);
    setTimeout(apply, 40);
    setTimeout(apply, 120);
  }

  cancelChat(): void {
    if (this.chatRequestSub) {
      this.chatRequestSub.unsubscribe();
      this.chatRequestSub = null;
    }
    this.chatLoading = false;
  }

  private emitSession(): void {
    this.sessionChange.emit({
      documentId: this.activeDocumentIdForApi,
      filename: this.uploadedName,
    });
    this.persistActiveSelection();
  }

  private persistActiveSelection(): void {
    const scope = this.storageScope();
    if (!scope) {
      return;
    }
    persistActiveSelection(scope, this.activeSessionKey);
  }

  private restoreActiveSelection(): void {
    const scope = this.storageScope();
    if (!scope) {
      return;
    }
    const key = restoreActiveSelectionKey(scope);
    if (!key) {
      return;
    }
    const entry = this.history.find((h) => h.key === key);
    if (!entry) {
      persistActiveSelection(scope, null);
      return;
    }
    this.selectSession(entry);
  }

  get canSend(): boolean {
    return (
      !!this.activeDocumentIdForApi &&
      this.activeCenterTab === "chat" &&
      this.sessionMode !== "quiz_mode" &&
      !this.chatLoading &&
      !this.applyDemoLoading &&
      this.draft.trim().length > 0
    );
  }

  setCenterTab(tab: CenterTab): void {
    this.activeCenterTab = tab;
    if (tab === "chat") {
      this.scheduleThreadObserverAttach();
      this.stickThreadToBottom = true;
      this.scrollThreadToBottom(true);
    }
  }

  get studySettingsDirty(): boolean {
    return this.draftSessionMode !== this.sessionMode;
  }

  get sortedHistory(): PdfSessionSnapshot[] {
    return [...this.history].sort((a, b) => b.updatedAt - a.updatedAt);
  }

  displaySessionFilename(filename: string): string {
    return displaySessionFilenameValue(filename);
  }

  labelForMode(mode: SessionMode): string {
    return this.modeOptions.find((x) => x.id === mode)?.label ?? mode;
  }

  get insightWeakLine(): string {
    return insightLine(this.weakTopicsFromQuiz, this.quizFocusTopics, 5);
  }

  get insightStrongLine(): string {
    return insightLine(this.strongTopicsFromQuiz, this.quizChallengeTopics, 4);
  }

  get insightSuggestionLine(): string {
    const tips = (this.insightTipLines || []).map((s) => String(s).trim()).filter(Boolean);
    return tips.length ? tips[0] : "";
  }

  get insightShortTip(): string {
    return shortTip(this.insightSuggestionLine, 160);
  }

  get showInsightStrip(): boolean {
    return (
      this.hasLocalSession &&
      (!!this.insightWeakLine || !!this.insightStrongLine || !!this.insightSuggestionLine)
    );
  }

  get lastAssistantMessage(): AssistantMessage | null {
    const list = this.tutorMessages;
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].role !== "user") {
        return list[i];
      }
    }
    return null;
  }

  get composerPlaceholder(): string {
    if (!this.hasSession) {
      return "Upload a PDF first…";
    }
    if (this.sessionMode === "quiz_mode") {
      return "No chat in Quiz mode — open the Quiz tab.";
    }
    return "Type your message…";
  }

  private personalizationOpts(): { focusTopics: string[]; challengeTopics: string[] } {
    return buildPersonalizationOpts(this.quizFocusTopics, this.quizChallengeTopics);
  }

  private summaryPrefsForCurrentSettings(): {
    summaryStyle: SummaryStylePref;
    summaryFormat: SummaryFormatPref;
    summaryLength: SummaryLengthPref;
  } {
    return summaryPrefsForMode(this.sessionMode);
  }

  private stripLegacyModePreviewMessages(list: AssistantMessage[]): AssistantMessage[] {
    return stripLegacyModePreviewMessages(list);
  }

  private setError(msg: string | null): void {
    this.error = msg;
  }

  private newLocalKey(): string {
    return generateLocalSessionKey();
  }

  private resetForUserSwitch(): void {
    this.cancelChat();
    this.history = [];
    this.tutorMessages = [];
    this.documentId = null;
    this.uploadedName = null;
    this.activeSessionKey = null;
    this.summaryOutput = "";
    this.explainOutput = "";
    this.examOutput = "";
    this.error = null;
    this.draft = "";
    this.sessionMode = "tutor_chat";
    this.draftSessionMode = "tutor_chat";
    this.activeCenterTab = "chat";
    this.quizGenState = "idle";
    this.emitSession();
  }

  private async reloadHistoryForUser(): Promise<void> {
    const scope = this.storageScope();
    if (!scope) {
      this.history = [];
      return;
    }

    let serverOk = false;
    let serverRows: PdfSessionSnapshot[] = [];
    try {
      const res = await this.api.getPdfSessionHistory().toPromise();
      serverRows = mapServerPdfSessionItems(res?.items || []);
      serverOk = true;
    } catch {
      serverOk = false;
    }

    if (serverOk) {
      const local = loadPdfSessionHistory(scope);
      const merged = mergePdfSessionHistories(serverRows, local);
      this.history = savePdfSessionHistory(scope, merged);
    } else {
      this.history = savePdfSessionHistory(scope, loadPdfSessionHistory(scope));
    }
    this.restoreActiveSelection();
    this.cdr.markForCheck();
  }

  private loadHistory(): void {
    const scope = this.storageScope();
    if (!scope) {
      this.history = [];
      return;
    }
    this.history = savePdfSessionHistory(scope, loadPdfSessionHistory(scope));
  }

  private saveHistory(): void {
    const scope = this.storageScope();
    if (!scope) {
      this.history = [];
      return;
    }
    this.history = savePdfSessionHistory(scope, this.history);
  }

  private upsertActiveSnapshot(): void {
    this.history = upsertActivePdfSnapshot(this.history, {
      documentId: this.documentId,
      activeSessionKey: this.activeSessionKey,
      uploadedName: this.uploadedName,
      tutorMessages: this.tutorMessages,
      summaryOutput: this.summaryOutput,
      explainOutput: this.explainOutput,
      examOutput: this.examOutput,
      sessionMode: this.sessionMode,
      explainLevel: this.explainLevel,
    });
    this.saveHistory();
  }

  private parseSessionMode(entry: PdfSessionSnapshot): SessionMode {
    return parseStoredSessionMode(entry);
  }

  selectSession(entry: PdfSessionSnapshot): void {
    selectSessionAction(this, entry);
  }

  private async verifyBackendSessionAfterSelect(seq: number, docId: string): Promise<void> {
    await verifyBackendSessionAfterSelectAction(this, seq, docId);
  }

  removeSession(event: MouseEvent, key: string): void {
    removeSessionAction(this, event, key);
  }

  onFileChange(event: Event): void {
    onFileChangeAction(this, event);
  }

  onShellDragOver(e: DragEvent): void {
    onShellDragOverAction(this, e);
  }

  onShellDragLeave(e: DragEvent): void {
    onShellDragLeaveAction(this, e);
  }

  onShellDrop(e: DragEvent): void {
    onShellDropAction(this, e);
  }

  private uploadFile(f: File | null): void {
    uploadFileAction(this, f);
  }

  private async runUpload(file: File): Promise<void> {
    await runUploadAction(this, file, MAX_QUIZ_QUESTIONS);
  }

  sendMessage(): void {
    sendMessageAction(this);
  }

  onComposerKeydown(ev: KeyboardEvent): void {
    onComposerKeydownAction(this, ev);
  }

  async runStarterAction(kind: "ask_pdf" | "quick_summary" | "explain_simple" | "exam_focus" | "quiz"): Promise<void> {
    await runStarterActionAction(this, kind);
  }

  newDocument(): void {
    newDocumentAction(this);
  }

  private syncDraftFromApplied(): void {
    this.draftSessionMode = this.sessionMode;
  }

  private clearSettingsFlash(): void {
    if (this.settingsFlashTimer) {
      clearTimeout(this.settingsFlashTimer);
      this.settingsFlashTimer = null;
    }
    this.settingsAppliedMessage = null;
  }

  pickDraftMode(mode: SessionMode): void {
    this.draftSessionMode = mode;
    this.settingsAppliedMessage = null;
  }

  async applyStudySettings(): Promise<void> {
    await applyStudySettingsAction(this);
  }

  private applyStudySettingsIfNeeded(): void {
    applyStudySettingsIfNeededAction(this);
  }

  private showSettingsHint(msg: string): void {
    showSettingsHintAction(this, msg);
  }

  private responseLooksLikeExpiredPdfSession(text: string): boolean {
    return looksLikeExpiredPdfSessionReply(text);
  }

  private quizMaxTopicsForCurrentLevel(questionCount: number, focusCount: number, challengeCount: number): number {
    return quizMaxTopicsForLevel(questionCount, focusCount, challengeCount, MAX_QUIZ_QUESTIONS);
  }

  private dropServerDocumentFromState(lostId: string): void {
    dropServerDocumentFromStateAction(this, lostId);
  }

  async generateQuizFromSidebar(): Promise<void> {
    await generateQuizFromSidebarAction(this, MAX_QUIZ_QUESTIONS);
  }

  openQuizModal(): void {
    this.quizModalOpen = true;
  }

  closeQuizModal(): void {
    this.quizModalOpen = false;
  }

  applyQuizModal(): void {
    this.numQuestions = Math.max(1, Math.min(MAX_QUIZ_QUESTIONS, Math.round(this.numQuestions)));
    this.quizModalOpen = false;
  }

}
