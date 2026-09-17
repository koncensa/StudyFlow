// cmp: pdf-assistant-utils | tr: pdf asistan yardımcı tipler ve küçük fonksiyonlar / en: pdf assistant helper types and small functions

import { SessionMode } from "./pdf-assistant.state";

export type SummaryStylePref = "concise" | "balanced" | "detailed";
export type SummaryFormatPref = "mixed" | "bullets" | "prose";
export type SummaryLengthPref = "short" | "medium" | "long";

export function displaySessionFilenameValue(filename: string): string {
  const safe = String(filename || "").trim();
  const withoutPdf = safe.replace(/\.pdf$/i, "").trim();
  return withoutPdf || safe || "document";
}

export function insightLine(primary: string[], fallback: string[], maxItems: number): string {
  const raw = (primary || []).map((s) => String(s).trim()).filter(Boolean);
  if (raw.length) {
    return raw.slice(0, maxItems).join(", ");
  }
  const fb = (fallback || []).map((s) => String(s).trim()).filter(Boolean);
  return fb.length ? fb.slice(0, maxItems).join(", ") : "";
}

export function shortTip(text: string, maxLength = 160): string {
  const s = String(text || "").trim();
  if (!s) {
    return "";
  }
  return s.length > maxLength ? `${s.slice(0, maxLength - 3)}…` : s;
}

export function summaryPrefsForMode(mode: SessionMode): {
  summaryStyle: SummaryStylePref;
  summaryFormat: SummaryFormatPref;
  summaryLength: SummaryLengthPref;
} {
  if (mode === "quick_summary") {
    return { summaryStyle: "balanced", summaryFormat: "bullets", summaryLength: "medium" };
  }
  if (mode === "explain_simple") {
    return { summaryStyle: "detailed", summaryFormat: "mixed", summaryLength: "medium" };
  }
  if (mode === "exam_focus") {
    return { summaryStyle: "detailed", summaryFormat: "bullets", summaryLength: "long" };
  }
  return { summaryStyle: "balanced", summaryFormat: "mixed", summaryLength: "medium" };
}

const EXPIRED_PDF_HINTS = [
  "pdf session expired",
  "session expired or invalid",
  "upload the pdf again",
  "pdf was not found",
  "pdf session is missing on the server",
  "bu pdf oturumu sona erdi",
  "bu pdf oturumu bulunamadi",
];

export function looksLikeExpiredPdfSessionReply(text: string): boolean {
  const s = String(text || "").trim().toLowerCase();
  if (!s) {
    return false;
  }
  return EXPIRED_PDF_HINTS.some((hint) => s.includes(hint));
}

export function quizMaxTopicsForLevel(
  questionCount: number,
  focusCount: number,
  challengeCount: number,
  maxQuizQuestions: number
): number {
  const n = Math.max(1, Math.min(maxQuizQuestions, Math.round(Number(questionCount) || 1)));
  const base = Math.min(8, Math.max(4, Math.round(n * 0.7) + 1));
  const focusBoost = focusCount > 0 ? 1 : 0;
  const challengeBoost = challengeCount > 0 ? 1 : 0;
  return Math.max(2, Math.min(12, base + focusBoost + challengeBoost));
}
