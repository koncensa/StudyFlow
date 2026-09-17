import { HttpErrorResponse } from "@angular/common/http";

/** Quiz route may return `{ success: false, error, message }` instead of `{ detail }`. */
export function readStructuredQuizError(err: unknown): { error: string; message: string } | null {
  if (!(err instanceof HttpErrorResponse) || err.error == null || typeof err.error !== "object") {
    return null;
  }
  const body = err.error as { success?: unknown; error?: unknown; message?: unknown };
  if (body.success !== false || typeof body.error !== "string") {
    return null;
  }
  const message = typeof body.message === "string" && body.message.trim() ? body.message.trim() : body.error;
  return { error: body.error, message };
}

/** Backend quiz/PDF session errors that mean the in-memory document is gone. */
export function isQuizDocumentGoneError(err: unknown): boolean {
  const s = readStructuredQuizError(err);
  if (!s) {
    return false;
  }
  return (
    s.error === "missing_document" ||
    s.error === "expired_document" ||
    s.error === "invalid_document"
  );
}

export function isInvalidDocumentQuizError(err: unknown): boolean {
  return isQuizDocumentGoneError(err);
}

/**
 * FastAPI often returns `{ detail: string | ValidationError[] }`.
 * Angular's HttpClient surfaces this on `HttpErrorResponse.error`.
 */
export function readApiError(err: unknown, fallback: string): string {
  if (err instanceof HttpErrorResponse) {
    const structured = readStructuredQuizError(err);
    if (structured) {
      return structured.message;
    }
    const body = err.error;
    if (typeof body === "string" && body.trim()) {
      return body.length > 280 ? `${body.slice(0, 277)}…` : body;
    }
    if (body && typeof body === "object") {
      const topMsg = (body as { message?: unknown }).message;
      if (typeof topMsg === "string" && topMsg.trim()) {
        return topMsg.trim();
      }
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail;
      }
      if (Array.isArray(detail)) {
        const parts = detail
          .map((x: { msg?: string; loc?: unknown }) => {
            const m = typeof x?.msg === "string" ? x.msg : "";
            return m.trim();
          })
          .filter(Boolean);
        if (parts.length) {
          return parts.slice(0, 5).join("; ");
        }
      }
    }
    if (err.status === 0) {
      return "Network error — is the API running?";
    }
    if (err.status === 403) {
      return "Access denied — try signing out and back in so your account matches the request.";
    }
    if (err.message) {
      return err.message;
    }
  }
  const any = err as { message?: string } | null;
  if (any && typeof any.message === "string" && any.message.trim()) {
    return any.message;
  }
  return fallback;
}
