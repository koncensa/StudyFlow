import { environment } from "../../environments/environment";
import { historyStorageKey, pdfSessionScope } from "./pdf-session-storage";

/** When quiz fails from the Quiz tab, mirror PDF assistant: remove dead ids from saved sessions. */
export function stripDocumentFromLocalHistory(
  userId: number,
  lostId: string,
  email?: string | null
): void {
  const scope = pdfSessionScope(userId, email);
  if (!scope) {
    return;
  }
  const trimmed = (lostId || "").trim();
  if (!trimmed) {
    return;
  }
  try {
    const key = historyStorageKey(scope);
    const raw = localStorage.getItem(key);
    if (!raw) {
      return;
    }
    const parsed = JSON.parse(raw) as { documentId?: string }[];
    if (!Array.isArray(parsed)) {
      return;
    }
    let changed = false;
    const lostNorm = normalizeDocumentId(trimmed);
    if (!lostNorm) {
      return;
    }
    const next = parsed.map((row) => {
      if (row && normalizeDocumentId(row.documentId) === lostNorm) {
        changed = true;
        return { ...row, documentId: "", updatedAt: Date.now() };
      }
      return row;
    });
    if (changed) {
      localStorage.setItem(key, JSON.stringify(next));
    }
  } catch {
    /* ignore */
  }
}

/** Non-empty trimmed server document id, or null. */
export function normalizeDocumentId(value: unknown): string | null {
  if (value == null) {
    return null;
  }
  const s = String(value).trim();
  return s.length > 0 ? s : null;
}

/** Dev-only tracing for PDF ↔ quiz flow. */
export function logQuizDocumentFlow(step: string, data: Record<string, unknown>): void {
  if (environment.production) {
    return;
  }
  console.log(`[StudyFlow quiz] ${step}`, data);
}
