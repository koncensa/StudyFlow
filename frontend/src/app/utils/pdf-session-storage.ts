import { isValidStudyflowUserId } from "./session-user";

/** Per-account local cache scope (user id alone is not enough when ids are reused). */
export interface PdfSessionStorageScope {
  userId: number;
  email: string;
}

const HISTORY_KEY_PREFIX_V2 = "studyflow_pdf_sessions_v2_";
const ACTIVE_SESSION_KEY_PREFIX_V2 = "studyflow_pdf_active_key_v2_";

export function pdfSessionScope(
  userId: number | null | undefined,
  email: string | null | undefined
): PdfSessionStorageScope | null {
  if (!isValidStudyflowUserId(userId)) {
    return null;
  }
  const em = (email || "").trim().toLowerCase();
  if (!em) {
    return null;
  }
  return { userId, email: em };
}

function scopeSlug(scope: PdfSessionStorageScope): string {
  return `${scope.userId}_${encodeURIComponent(scope.email).slice(0, 120)}`;
}

export function historyStorageKey(scope: PdfSessionStorageScope): string {
  return `${HISTORY_KEY_PREFIX_V2}${scopeSlug(scope)}`;
}

export function activeSessionStorageKey(scope: PdfSessionStorageScope): string {
  return `${ACTIVE_SESSION_KEY_PREFIX_V2}${scopeSlug(scope)}`;
}
