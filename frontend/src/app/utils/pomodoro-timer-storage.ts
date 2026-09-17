/** Per-account local timer state (user id + email — same pattern as quiz/PDF history). */
export function buildPomodoroTimerStorageKey(
  userId: number | null | undefined,
  email: string | null | undefined
): string | null {
  const uid = Number(userId);
  if (!Number.isFinite(uid) || uid < 1) {
    return null;
  }
  const em = (email || "").trim().toLowerCase();
  if (!em) {
    return null;
  }
  return `studyflow:pomodoro-timer:v3:${uid}_${encodeURIComponent(em).slice(0, 120)}`;
}

/** Global key from before per-user scoping — safe to drop after migration. */
export function clearLegacyPomodoroTimerStorage(): void {
  try {
    localStorage.removeItem("studyflow_pomodoro_timer_v2");
  } catch {
    // Best effort only.
  }
}
