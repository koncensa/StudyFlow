// cmp: pdf-assistant-state | tr: pdf oturum state tipleri ve localStorage anahtarları / en: pdf session state types and localStorage keys

import { ExplainLevel, PdfSessionHistoryItemDto } from "../../models/types";
import { normalizeDocumentId } from "../../utils/document-id";
import {
  activeSessionStorageKey,
  historyStorageKey,
  PdfSessionStorageScope,
  pdfSessionScope,
} from "../../utils/pdf-session-storage";
import { isValidStudyflowUserId } from "../../utils/session-user";

export { isValidStudyflowUserId, PdfSessionStorageScope, pdfSessionScope };

export interface AssistantMessage {
  role: "user" | "assistant";
  content: string;
}

/** UI mode (includes Quiz); maps to API for Apply-mode generation */
export type SessionMode = "tutor_chat" | "quick_summary" | "explain_simple" | "exam_focus" | "quiz_mode";

/** Center column view — chat vs last mode output (not mixed into tutor thread). */
export type CenterTab = "chat" | "summary" | "explain" | "exam";

export interface PdfSessionSnapshot {
  /** Client-side session row id (persists across refresh). */
  key: string;
  documentId: string;
  filename: string;
  updatedAt: number;
  /** First time this row was created (optional for older saved data). */
  createdAt?: number;
  /** Tutor dialogue only (user + assistant turns). */
  messages: AssistantMessage[];
  /** Latest Quick Summary result (replaced on each Apply). */
  summaryOutput?: string | null;
  /** Latest Explain Simply result. */
  explainOutput?: string | null;
  /** Latest Exam Focus result. */
  examOutput?: string | null;
  /** @deprecated Migrated to summaryOutput on load when newer fields absent. */
  modePreviewText?: string | null;
  sessionMode?: SessionMode;
  explainLevel?: ExplainLevel;
}

export interface PersonalizationOptions {
  focusTopics: string[];
  challengeTopics: string[];
}

const MAX_HISTORY = 32;

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

export function mapServerPdfSessionItems(items: PdfSessionHistoryItemDto[]): PdfSessionSnapshot[] {
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter((x) => x && typeof x.document_id === "string" && Array.isArray(x.messages))
    .map((item) => {
      const docId = normalizeDocumentId(item.document_id) || item.document_id.trim();
      const updatedMs = item.updated_at ? Date.parse(item.updated_at) : NaN;
      return {
        key: (item.key || docId).trim(),
        documentId: docId,
        filename: (item.filename || "document.pdf").trim() || "document.pdf",
        updatedAt: Number.isFinite(updatedMs) ? updatedMs : Date.now(),
        messages: item.messages
          .filter((m) => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string")
          .map((m) => ({ role: m.role, content: m.content })),
      };
    })
    .filter((x) => x.messages.length > 0);
}

/** Merge server rows with this account's local cache (survives logout; user can still delete rows in UI). */
export function mergePdfSessionHistories(
  server: PdfSessionSnapshot[],
  local: PdfSessionSnapshot[]
): PdfSessionSnapshot[] {
  const byDoc = new Map<string, PdfSessionSnapshot>();
  const localOnly: PdfSessionSnapshot[] = [];

  for (const row of server) {
    const nd = normalizeDocumentId(row.documentId);
    if (nd) {
      byDoc.set(nd, { ...row, documentId: nd });
    }
  }

  for (const row of local) {
    const nd = normalizeDocumentId(row.documentId);
    if (!nd) {
      localOnly.push(row);
      continue;
    }
    const prev = byDoc.get(nd);
    if (!prev || row.updatedAt > prev.updatedAt) {
      byDoc.set(nd, { ...row, documentId: nd });
    } else if (row.updatedAt === prev.updatedAt && (row.messages?.length || 0) > (prev.messages?.length || 0)) {
      byDoc.set(nd, { ...row, documentId: nd });
    }
  }

  return [...localOnly, ...Array.from(byDoc.values())]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_HISTORY);
}

export function loadPdfSessionHistory(scope: PdfSessionStorageScope): PdfSessionSnapshot[] {
  try {
    const raw = localStorage.getItem(historyStorageKey(scope));
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as PdfSessionSnapshot[];
    if (!Array.isArray(parsed)) {
      return [];
    }
    const rows = parsed.filter(
      (x) => x && typeof x.key === "string" && typeof x.documentId === "string" && Array.isArray(x.messages)
    );
    const byDoc = new Map<string, PdfSessionSnapshot>();
    const withoutServerId: PdfSessionSnapshot[] = [];
    for (const h of rows) {
      const nd = normalizeDocumentId(h.documentId);
      if (!nd) {
        withoutServerId.push(h);
        continue;
      }
      const prev = byDoc.get(nd);
      if (!prev || h.updatedAt > prev.updatedAt) {
        byDoc.set(nd, { ...h, documentId: nd });
      }
    }
    return [...withoutServerId, ...Array.from(byDoc.values())];
  } catch {
    return [];
  }
}

export function savePdfSessionHistory(
  scope: PdfSessionStorageScope,
  history: PdfSessionSnapshot[]
): PdfSessionSnapshot[] {
  const trimmed = history.slice(0, MAX_HISTORY);
  try {
    localStorage.setItem(historyStorageKey(scope), JSON.stringify(trimmed));
  } catch {
    /* ignore quota */
  }
  return trimmed;
}

export function persistActiveSelection(scope: PdfSessionStorageScope, sessionKey: string | null): void {
  try {
    const k = activeSessionStorageKey(scope);
    if (sessionKey) {
      localStorage.setItem(k, sessionKey);
    } else {
      localStorage.removeItem(k);
    }
  } catch {
    /* private mode / quota */
  }
}

export function restoreActiveSelectionKey(scope: PdfSessionStorageScope): string | null {
  try {
    return localStorage.getItem(activeSessionStorageKey(scope));
  } catch {
    return null;
  }
}

export function stripLegacyModePreviewMessages(list: AssistantMessage[]): AssistantMessage[] {
  const isLegacyPreview = (c: string) =>
    c.includes("Mode preview — settings saved:") || c.includes("Mod önizlemesi — ayarlar kayıtlı:");
  return list.filter((m) => !(m.role === "assistant" && isLegacyPreview(m.content)));
}

export function parseStoredSessionMode(entry: PdfSessionSnapshot): SessionMode {
  if (entry.sessionMode) {
    return entry.sessionMode;
  }
  const legacy = (entry as { studyMode?: string }).studyMode;
  if (legacy === "quiz_coach" || legacy === "quiz_mode") {
    return "quiz_mode";
  }
  if (legacy && ["tutor_chat", "quick_summary", "explain_simple", "exam_focus"].includes(legacy)) {
    return legacy as SessionMode;
  }
  return "tutor_chat";
}

export function generateLocalSessionKey(): string {
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function buildPersonalizationOpts(
  quizFocusTopics: string[],
  quizChallengeTopics: string[]
): PersonalizationOptions {
  return {
    focusTopics: (quizFocusTopics || [])
      .map((s) => String(s).trim())
      .filter(Boolean)
      .slice(0, 12),
    challengeTopics: (quizChallengeTopics || [])
      .map((s) => String(s).trim())
      .filter(Boolean)
      .slice(0, 10),
  };
}

export function upsertActivePdfSnapshot(
  history: PdfSessionSnapshot[],
  payload: {
    documentId: string | null;
    activeSessionKey: string | null;
    uploadedName: string | null;
    tutorMessages: AssistantMessage[];
    summaryOutput: string;
    explainOutput: string;
    examOutput: string;
    sessionMode: SessionMode;
    explainLevel: ExplainLevel;
  }
): PdfSessionSnapshot[] {
  const { documentId, activeSessionKey } = payload;
  if (!documentId || !activeSessionKey) {
    return history;
  }
  const filtered = history.filter((h) => h.documentId !== documentId || h.key === activeSessionKey);
  const idx = filtered.findIndex((h) => h.key === activeSessionKey);
  const prevRow = idx >= 0 ? filtered[idx] : null;
  const row: PdfSessionSnapshot = {
    key: activeSessionKey,
    documentId,
    filename: payload.uploadedName || "document.pdf",
    updatedAt: Date.now(),
    createdAt: prevRow?.createdAt ?? prevRow?.updatedAt ?? Date.now(),
    messages: payload.tutorMessages.map((m) => ({ ...m })),
    summaryOutput: payload.summaryOutput || undefined,
    explainOutput: payload.explainOutput || undefined,
    examOutput: payload.examOutput || undefined,
    sessionMode: payload.sessionMode,
    explainLevel: payload.explainLevel,
  };
  if (idx >= 0) {
    filtered[idx] = row;
  } else {
    filtered.unshift(row);
  }
  return filtered;
}
