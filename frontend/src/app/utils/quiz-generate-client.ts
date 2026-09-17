import { HttpErrorResponse } from "@angular/common/http";
import { Observable, throwError, TimeoutError } from "rxjs";
import { catchError, timeout } from "rxjs/operators";

import { QuizGenerateResponse, QuizQuestion } from "../models/types";
import { readStructuredQuizError } from "./api-error";

export function isCompleteQuizResponse(
  res: QuizGenerateResponse | null | undefined,
  expectedCount: number
): boolean {
  const qs = res?.questions;
  if (!qs || !Array.isArray(qs) || qs.length === 0) {
    return false;
  }
  const target = Math.max(1, Math.round(Number(expectedCount) || 1));
  if (qs.length !== target) {
    return false;
  }
  if (!qs.every((q) => isValidQuizQuestionShape(q))) {
    return false;
  }
  const apiTotal = res?.total_questions;
  // Accept when the list length matches the request; only reject obvious API bugs.
  if (apiTotal != null && apiTotal > 0 && apiTotal !== qs.length) {
    return false;
  }
  return true;
}

export function isValidQuizQuestionShape(q: QuizQuestion): boolean {
  const id = String((q as { id?: string }).id ?? "").trim();
  if (id.length < 4) {
    return false;
  }
  const t = String(q?.question_text || "").trim();
  if (t.length < 10) {
    return false;
  }
  const opts = (q?.options || []).map((o) => String(o).trim()).filter((s) => s.length > 0);
  if (opts.length !== 4) {
    return false;
  }
  if (opts.some((s) => s.length < 2 || s.length > 320)) {
    return false;
  }
  const ca = String(q?.correct_answer || "").trim();
  if (!ca) {
    return false;
  }
  const inOpts =
    opts.includes(ca) || opts.some((o) => o.toLowerCase() === ca.toLowerCase());
  if (!inOpts) {
    return false;
  }
  const topic = String(q?.topic || "").trim();
  return topic.length >= 2;
}

const MSG_TIMEOUT_TR =
  "İstek zaman aşımına uğradı (model yanıtı çok uzun sürdü). Ollama çalışıyor mu kontrol edin; gerekirse soru sayısını azaltıp tekrar deneyin.";

export function mapQuizGenerateFailure(err: unknown): string {
  if (err instanceof TimeoutError) {
    return MSG_TIMEOUT_TR;
  }
  if (err instanceof HttpErrorResponse) {
    const structured = readStructuredQuizError(err);
    if (structured?.message) {
      return structured.message;
    }
    const body = err.error;
    if (typeof body === "string" && body.trim()) {
      return body.length > 280 ? `${body.slice(0, 277)}…` : body;
    }
    if (body && typeof body === "object") {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail.trim();
      }
    }
    if (err.status === 503) {
      return "Sunucu quiz üretemedi (Ollama yavaş/kapalı veya PDF metni yetersiz olabilir). Biraz sonra tekrar deneyin.";
    }
  }
  const msg = (err as { message?: string })?.message;
  if (msg === "QUIZ_INCOMPLETE") {
    return "İstenen soru sayısı üretilemedi. Lütfen tekrar deneyin.";
  }
  if (msg === "QUIZ_DEADLINE" || msg === "QUIZ_TIMEOUT") {
    return MSG_TIMEOUT_TR;
  }
  if (typeof msg === "string" && msg.trim()) {
    return msg.trim();
  }
  return "Quiz oluşturulamadı. Lütfen tekrar deneyin.";
}

/**
 * Single HTTP round-trip with a hard RxJS timeout so the UI never waits forever.
 * (Retries used to re-hit a slow backend 3× and matched exact counts — felt “stuck”.)
 */
export async function fetchQuizWithDeadline<T extends QuizGenerateResponse>(
  factory: (timeoutMs: number) => Observable<T>,
  expectedQuestions: number,
  deadlineMs: number
): Promise<T> {
  const target = Math.max(1, Math.round(Number(expectedQuestions) || 1));
  // Adaptive timeout: at least a model-sized budget, never above caller deadline (e.g. quizGenerateDeadlineMs).
  const floorMs = 30000;
  const dm = Math.max(floorMs, Math.round(Number(deadlineMs) || 300000));
  const recommended = 45000 + target * 8000;
  const soft = Math.max(floorMs, recommended);
  const cap = Math.min(dm, Math.max(soft, dm));
  const res = await factory(cap)
    .pipe(
      timeout(cap),
      catchError((e) => {
        if (e instanceof TimeoutError) {
          return throwError(() => new Error("QUIZ_TIMEOUT"));
        }
        return throwError(() => e);
      })
    )
    .toPromise();
  if (res && isCompleteQuizResponse(res, expectedQuestions)) {
    return res;
  }
  throw new Error("QUIZ_INCOMPLETE");
}
