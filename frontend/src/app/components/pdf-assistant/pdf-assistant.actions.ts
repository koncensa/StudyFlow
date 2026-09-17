// cmp: pdf-assistant-actions | tr: pdf asistan api çağrıları (yükle, özet, chat, quiz) / en: pdf assistant api actions upload summary chat quiz

import { environment } from "../../../environments/environment";
import { ExplainLevel } from "../../models/types";
import { isQuizDocumentGoneError, readApiError } from "../../utils/api-error";
import { logQuizDocumentFlow, normalizeDocumentId } from "../../utils/document-id";
import { fetchQuizWithDeadline, mapQuizGenerateFailure } from "../../utils/quiz-generate-client";
import { AssistantMessage, MAX_UPLOAD_BYTES, PdfSessionSnapshot, SessionMode } from "./pdf-assistant.state";

export function selectSessionAction(ctx: any, entry: PdfSessionSnapshot): void {
  ctx.cancelChat();
  ctx.setError(null);
  ctx.activeSessionKey = entry.key;
  const docId = normalizeDocumentId(entry.documentId);
  logQuizDocumentFlow("history item selected", {
    selected_history_key: entry.key,
    document_id_from_row: entry.documentId,
    active_document_id: docId,
  });

  // burada server id'yi hemen açmıyoruz; önce status doğrulaması gelsin.
  ctx.documentId = null;
  ctx.uploadedName = entry.filename;
  const cleaned = ctx.stripLegacyModePreviewMessages(entry.messages.map((m: AssistantMessage) => ({ ...m })));
  ctx.tutorMessages = cleaned;

  let summary = (entry.summaryOutput || "").trim();
  const legacyPrev = (entry.modePreviewText || "").trim();
  if (!summary && legacyPrev) {
    summary = legacyPrev;
  }
  ctx.summaryOutput = summary;
  ctx.explainOutput = (entry.explainOutput || "").trim();
  ctx.examOutput = (entry.examOutput || "").trim();

  const migratedLegacyPreview = !!legacyPrev && !(entry.summaryOutput || "").trim();
  if (migratedLegacyPreview || cleaned.length !== entry.messages.length) {
    const idx = ctx.history.findIndex((h: PdfSessionSnapshot) => h.key === entry.key);
    if (idx >= 0) {
      ctx.history[idx] = {
        ...ctx.history[idx],
        messages: cleaned.map((m: AssistantMessage) => ({ ...m })),
        summaryOutput: summary || undefined,
        explainOutput: ctx.explainOutput || undefined,
        examOutput: ctx.examOutput || undefined,
        modePreviewText: undefined,
        updatedAt: Date.now(),
      };
      ctx.saveHistory();
    }
  }

  ctx.sessionMode = ctx.parseSessionMode(entry);
  ctx.explainLevel = "normal";
  ctx.activeCenterTab = "chat";
  ctx.syncDraftFromApplied();
  ctx.clearSettingsFlash();
  ctx.draft = "";
  ctx.quizReady.emit(null);
  ctx.emitSession();
  ctx.stickThreadToBottom = true;
  ctx.scheduleThreadObserverAttach();
  ctx.scrollThreadToBottom(true);

  if (!docId) {
    ctx.historySessionCheckPending = false;
    ctx.setError(ctx.i18n.t("quiz.historySessionExpired"));
    return;
  }
  ctx.historySessionCheckPending = true;
  ctx.cdr.markForCheck();
  const seq = ++ctx.historyVerifySeq;
  void ctx.verifyBackendSessionAfterSelect(seq, docId);
}

export async function verifyBackendSessionAfterSelectAction(ctx: any, seq: number, docId: string): Promise<void> {
  try {
    const res = await ctx.api.checkPdfDocumentStatus(docId, ctx.userId).toPromise();
    if (seq !== ctx.historyVerifySeq) {
      return;
    }
    if (res?.ok) {
      ctx.documentId = docId;
      logQuizDocumentFlow("history verify ok", {
        selected_history_key: ctx.activeSessionKey,
        active_document_id: docId,
      });
      ctx.emitSession();
      ctx.cdr.markForCheck();
      return;
    }
    logQuizDocumentFlow("history verify failed", {
      selected_history_key: ctx.activeSessionKey,
      document_id: docId,
      error: res?.error ?? "(no error code)",
    });
    ctx.setError(ctx.i18n.t("quiz.historySessionExpired"));
    ctx.cdr.markForCheck();
  } catch (e: unknown) {
    if (seq !== ctx.historyVerifySeq) {
      return;
    }
    logQuizDocumentFlow("history verify request error", {
      selected_history_key: ctx.activeSessionKey,
      document_id: docId,
      detail: String(e),
    });
    ctx.setError(ctx.i18n.t("quiz.historySessionExpired"));
    ctx.cdr.markForCheck();
  } finally {
    if (seq === ctx.historyVerifySeq) {
      ctx.historySessionCheckPending = false;
      ctx.cdr.markForCheck();
    }
  }
}

export function removeSessionAction(ctx: any, event: MouseEvent, key: string): void {
  event.stopPropagation();
  ctx.history = ctx.history.filter((h: PdfSessionSnapshot) => h.key !== key);
  ctx.saveHistory();
  if (ctx.activeSessionKey === key) {
    ctx.newDocument();
  }
}

export function onFileChangeAction(ctx: any, event: Event): void {
  const input = event.target as HTMLInputElement;
  const f = input.files && input.files.length ? input.files[0] : null;
  input.value = "";
  ctx.uploadFile(f);
}

export function onShellDragOverAction(ctx: any, e: DragEvent): void {
  if (!ctx.canAcceptPdfDrop) {
    return;
  }
  e.preventDefault();
  e.stopPropagation();
  ctx.dragOver = true;
}

export function onShellDragLeaveAction(ctx: any, e: DragEvent): void {
  if (!ctx.canAcceptPdfDrop) {
    return;
  }
  e.preventDefault();
  e.stopPropagation();
  const related = e.relatedTarget as Node | null;
  if (related && (e.currentTarget as HTMLElement).contains(related)) {
    return;
  }
  ctx.dragOver = false;
}

export function onShellDropAction(ctx: any, e: DragEvent): void {
  if (!ctx.canAcceptPdfDrop) {
    return;
  }
  e.preventDefault();
  e.stopPropagation();
  ctx.dragOver = false;
  const f = e.dataTransfer?.files?.length ? e.dataTransfer.files[0] : null;
  ctx.uploadFile(f || null);
}
//pdf upload yükleme
export function uploadFileAction(ctx: any, f: File | null): void {
  if (!f || ctx.uploadLoading) {
    return;
  }
  if (!f.name.toLowerCase().endsWith(".pdf")) {
    ctx.setError("PDF files only.");
    return;
  }
  if (f.size > MAX_UPLOAD_BYTES) {
    ctx.setError("File too large (max 50MB).");
    return;
  }
  ctx.setError(null);
  void ctx.runUpload(f);
}
//apiye gönderim
export async function runUploadAction(ctx: any, file: File, maxQuizQuestions: number): Promise<void> {
  ctx.historyVerifySeq++;
  ctx.historySessionCheckPending = false;
  ctx.uploadLoading = true;
  ctx.setError(null);
  try {
    const nq = Math.max(1, Math.min(maxQuizQuestions, Math.round(ctx.numQuestions)));
    const res = await ctx.api.uploadPdf(file, false, nq, ctx.userId, ctx.locale, false).toPromise();
    if (res?.error) {
      const msg =
        typeof (res as { message?: string }).message === "string" ? (res as { message: string }).message : "";
      ctx.setError(msg.trim() || String(res.error));
      return;
    }
    const id = normalizeDocumentId(res?.document_id);
    logQuizDocumentFlow("upload response", {
      uploaded_document_id: id,
      raw_document_id: res?.document_id,
      filename: res?.filename || file.name,
    });
    if (!id) {
      ctx.setError("Upload succeeded but no session id returned.");
      return;
    }

    // Aynı dosya ikinci kez yüklense bile kullanıcı için yeni bir çalışma oturumu açıyoruz.
    ctx.activeSessionKey = ctx.newLocalKey();
    ctx.tutorMessages = [];
    ctx.summaryOutput = "";
    ctx.explainOutput = "";
    ctx.examOutput = "";
    ctx.sessionMode = "tutor_chat";
    ctx.explainLevel = "normal" as ExplainLevel;
    ctx.documentId = id;
    ctx.uploadedName = res.filename || file.name;
    ctx.activeCenterTab = "chat";
    ctx.syncDraftFromApplied();
    ctx.clearSettingsFlash();
    ctx.quizReady.emit(null);
    ctx.quizModalOpen = false;
    ctx.upsertActiveSnapshot();
    logQuizDocumentFlow("upload complete emit session", {
      active_document_id: id,
      active_session_key: ctx.activeSessionKey,
      filename: ctx.uploadedName,
    });
    ctx.emitSession();
    ctx.stickThreadToBottom = true;
    ctx.scheduleThreadObserverAttach();
    ctx.scrollThreadToBottom(true);
  } catch (e: unknown) {
    ctx.setError(readApiError(e, "Upload failed."));
  } finally {
    ctx.uploadLoading = false;
    ctx.historySessionCheckPending = false;
  }
}

export function sendMessageAction(ctx: any): void {
  const text = ctx.draft.trim();
  if (!ctx.canSend || !text || !ctx.documentId) {
    return;
  }
  if (ctx.sessionMode === "quiz_mode") {
    return;
  }

  ctx.cancelChat();
  ctx.draft = "";
  ctx.stickThreadToBottom = true;
  ctx.tutorMessages = [...ctx.tutorMessages, { role: "user", content: text }];
  ctx.chatLoading = true;
  ctx.setError(null);
  ctx.scrollThreadToBottom(true);

  ctx.chatRequestSub = ctx.api
    .pdfChat(
      ctx.documentId,
      ctx.tutorMessages,
      ctx.userId,
      "tutor_chat",
      ctx.explainLevel,
      ctx.locale,
      ctx.personalizationOpts()
    )
    .subscribe(
      (res: { reply?: string }) => {
        const reply = (res?.reply || "").trim() || "No response."; //chatten gelen cevap ve ekrana yazdırma
        ctx.tutorMessages = [...ctx.tutorMessages, { role: "assistant", content: reply }];
        ctx.upsertActiveSnapshot();
        ctx.chatLoading = false;
        ctx.chatRequestSub = null;
        ctx.scrollThreadToBottom(true);
      },
      (e: unknown) => {
        ctx.chatLoading = false;
        ctx.chatRequestSub = null;
        ctx.setError(readApiError(e, "Chat request failed."));
        ctx.tutorMessages = [
          ...ctx.tutorMessages,
          {
            role: "assistant",
            content: "Sorry, the chat request failed. Is the API and Ollama running?",
          },
        ];
        ctx.scrollThreadToBottom(true);
      }
    );
}

export function onComposerKeydownAction(ctx: any, ev: KeyboardEvent): void {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    ctx.sendMessage();
  }
}

export async function runStarterActionAction(
  ctx: any,
  kind: "ask_pdf" | "quick_summary" | "explain_simple" | "exam_focus" | "quiz"
): Promise<void> {
  if (!ctx.activeDocumentIdForApi || ctx.chatLoading || ctx.applyDemoLoading) {
    return;
  }
  if (ctx.sessionMode === "quiz_mode" && kind !== "quiz") {
    return;
  }
  if (kind === "quiz") {
    ctx.openQuizTab.emit();
    return;
  }
  if (kind === "ask_pdf") {
    const q = "What are the main ideas in this PDF, and what should I prioritize while studying?";
    ctx.cancelChat();
    ctx.setCenterTab("chat");
    ctx.draft = q;
    ctx.sendMessage();
    return;
  }
  const mode: SessionMode =
    kind === "quick_summary" ? "quick_summary" : kind === "explain_simple" ? "explain_simple" : "exam_focus";
  ctx.draftSessionMode = mode;
  if (ctx.sessionMode === mode) {
    ctx.sessionMode = "tutor_chat";
  }
  await ctx.applyStudySettings();
}

export function newDocumentAction(ctx: any): void {
  ctx.historyVerifySeq++;
  ctx.historySessionCheckPending = false;
  ctx.cancelChat();
  ctx.documentId = null;
  ctx.uploadedName = null;
  ctx.tutorMessages = [];
  ctx.summaryOutput = "";
  ctx.explainOutput = "";
  ctx.examOutput = "";
  ctx.activeCenterTab = "chat";
  ctx.draft = "";
  ctx.activeSessionKey = null;
  ctx.sessionMode = "tutor_chat";
  ctx.explainLevel = "normal" as ExplainLevel;
  ctx.syncDraftFromApplied();
  ctx.clearSettingsFlash();
  ctx.setError(null);
  ctx.quizReady.emit(null);
  ctx.emitSession();
  ctx.stickThreadToBottom = true;
}

export async function applyStudySettingsAction(ctx: any): Promise<void> {
  if (!ctx.hasLocalSession || !ctx.activeDocumentIdForApi || !ctx.studySettingsDirty) {
    return;
  }
  if (ctx.draftSessionMode === "quiz_mode") {
    ctx.sessionMode = ctx.draftSessionMode;
    ctx.explainLevel = "normal" as ExplainLevel;
    ctx.upsertActiveSnapshot();
    return;
  }

  ctx.sessionMode = ctx.draftSessionMode;
  ctx.explainLevel = "normal" as ExplainLevel;
  ctx.upsertActiveSnapshot();

  ctx.applyDemoLoading = true;
  ctx.setError(null);
  ctx.clearSettingsFlash();
  ctx.stickThreadToBottom = true;

  if (ctx.sessionMode === "tutor_chat") {
    ctx.activeCenterTab = "chat";
    ctx.scrollThreadToBottom(true);
  } else if (ctx.sessionMode === "quick_summary") {
    ctx.activeCenterTab = "summary";
  } else if (ctx.sessionMode === "explain_simple") {
    ctx.activeCenterTab = "explain";
  } else if (ctx.sessionMode === "exam_focus") {
    ctx.activeCenterTab = "exam";
  }

  try {
    if (ctx.sessionMode === "tutor_chat") {
      ctx.showSettingsHint("Chat tab — messages use tutor style.");
      ctx.upsertActiveSnapshot();
      ctx.scrollThreadToBottom(true);
      return;
    }

    const res = await ctx.api
      .applyPdfStudyMode(ctx.activeDocumentIdForApi, ctx.userId, ctx.chatStudyMode, ctx.explainLevel, ctx.locale, {
        ...ctx.personalizationOpts(),
        ...ctx.summaryPrefsForCurrentSettings(),
      })
      .toPromise();
    const reply = (res?.reply || "").trim();
    if (ctx.responseLooksLikeExpiredPdfSession(reply)) {
      ctx.dropServerDocumentFromState(ctx.activeDocumentIdForApi);
      ctx.setError(ctx.i18n.t("quiz.pdfSessionMaybeExpired"));
      ctx.showSettingsHint("PDF session expired — please upload again.");
      return;
    }

    const body = reply || "No response from the model. Check Ollama and try again.";
    if (ctx.sessionMode === "quick_summary") {
      ctx.summaryOutput = body;
      ctx.showSettingsHint("Updated — Summary tab.");
    } else if (ctx.sessionMode === "explain_simple") {
      ctx.explainOutput = body;
      ctx.showSettingsHint("Updated — Explain tab.");
    } else if (ctx.sessionMode === "exam_focus") {
      ctx.examOutput = body;
      ctx.showSettingsHint("Updated — Exam tab.");
    }
    ctx.upsertActiveSnapshot();
  } catch (e: unknown) {
    ctx.setError(readApiError(e, "Could not run this mode."));
    ctx.showSettingsHint("Settings saved — try Apply again.");
  } finally {
    ctx.applyDemoLoading = false;
  }
}

export function applyStudySettingsIfNeededAction(ctx: any): void {
  if (ctx.studySettingsDirty) {
    ctx.sessionMode = ctx.draftSessionMode;
    ctx.explainLevel = "normal" as ExplainLevel;
    ctx.upsertActiveSnapshot();
  }
}

export function showSettingsHintAction(ctx: any, msg: string): void {
  ctx.clearSettingsFlash();
  ctx.settingsAppliedMessage = msg;
  ctx.settingsFlashTimer = setTimeout(() => {
    ctx.settingsAppliedMessage = null;
    ctx.settingsFlashTimer = null;
  }, 3800);
}

export function dropServerDocumentFromStateAction(ctx: any, lostId: string): void {
  const lostNorm = normalizeDocumentId(lostId);
  let changed = false;
  ctx.history = ctx.history.map((h: PdfSessionSnapshot) => {
    if (lostNorm && normalizeDocumentId(h.documentId) === lostNorm) {
      changed = true;
      return { ...h, documentId: "", updatedAt: Date.now() };
    }
    return h;
  });
  if (changed) {
    ctx.saveHistory();
  }
  ctx.documentId = null;
  ctx.historySessionCheckPending = false;
  ctx.emitSession();
}

export async function generateQuizFromSidebarAction(ctx: any, maxQuizQuestions: number): Promise<void> {
  if (ctx.quizGenState === "loading" || ctx.chatLoading) {
    return;
  }
  const docId = ctx.activeDocumentIdForApi;
  if (!docId) {
    logQuizDocumentFlow("generate quiz blocked", { reason: "no_document_id" });
    ctx.setError(ctx.i18n.t("quiz.noActiveSession"));
    ctx.quizGenState = "error";
    return;
  }

  logQuizDocumentFlow("generate quiz start", {
    active_document_id: docId,
    active_document_id_prefix: docId.slice(0, 16),
    selected_history_key: ctx.activeSessionKey,
  });

  ctx.applyStudySettingsIfNeeded();
  const nq = Math.max(1, Math.min(maxQuizQuestions, Math.round(ctx.numQuestions)));
  ctx.quizGenState = "loading";
  ctx.setError(null);

  try {
    const focus = (ctx.quizFocusTopics || [])
      .map((s: string) => String(s).trim())
      .filter(Boolean)
      .slice(0, 8);
    const challenge = (ctx.quizChallengeTopics || [])
      .map((s: string) => String(s).trim())
      .filter(Boolean)
      .slice(0, 6);
    const maxTopics = ctx.quizMaxTopicsForCurrentLevel(nq, focus.length, challenge.length);
    logQuizDocumentFlow("generate quiz client payload", {
      document_id: docId,
      user_id: ctx.userId,
      num_questions: nq,
      max_topics: maxTopics,
      focus_topics: focus,
      challenge_topics: challenge,
      locale: ctx.locale,
      difficulty: ctx.explainLevel,
    });

    const res = await fetchQuizWithDeadline(
      (_timeoutMs) =>
        ctx.api.generateQuizFromDocument(docId, ctx.userId, nq, maxTopics, focus, {
          challengeTopics: challenge.length ? challenge : undefined,
          locale: ctx.locale,
          difficulty: ctx.explainLevel,
        }),
      nq,
      environment.quizGenerateDeadlineMs
    );
    const tl = res?.time_limit_seconds;
    ctx.quizGenState = "success";
    ctx.quizReady.emit({
      questions: res.questions,
      time_limit_seconds: typeof tl === "number" && tl > 0 ? tl : undefined,
    });
    ctx.showSettingsHint(`Quiz hazır (${nq} soru). Quiz sekmesine geçildi.`);
    ctx.upsertActiveSnapshot();
    ctx.openQuizTab.emit();
    ctx.quizGenState = "idle";
  } catch (e: unknown) {
    if (isQuizDocumentGoneError(e)) {
      logQuizDocumentFlow("generate quiz failed", { reason: "document_gone" });
      ctx.dropServerDocumentFromState(docId);
      ctx.setError(ctx.i18n.t("quiz.pdfSessionMaybeExpired"));
    } else {
      ctx.setError(mapQuizGenerateFailure(e));
    }
    ctx.quizGenState = "error";
  } finally {
    // Bu blok bilinçli: loading'de takılı kalmasın diye son bir emniyet freni.
    if (ctx.quizGenState === "loading") {
      ctx.quizGenState = "error";
      if (!ctx.error) {
        ctx.setError("Quiz oluşturulamadı. Ollama kapalı olabilir veya istek yarıda kaldı — tekrar deneyin.");
      }
    }
    if (ctx.quizGenState === "success") {
      ctx.quizGenState = "idle";
    }
  }
}
