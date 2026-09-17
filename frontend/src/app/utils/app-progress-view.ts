import { StudyStats } from "../models/types";
import { PomodoroTimerSnapshot } from "../services/pomodoro";

export function formatProgressDurationSecondsText(total: number, tr: boolean): string {
  const s = Math.max(0, Math.floor(Number(total) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) {
    return tr ? `${h} sa ${m} dk` : `${h}h ${m}m`;
  }
  if (m > 0) {
    return tr ? `${m} dk ${r} sn` : `${m}m ${r}s`;
  }
  return tr ? `${r} sn` : `${r}s`;
}

export function progressFocusSecondsValue(
  stats: StudyStats | null | undefined,
  snapshot: PomodoroTimerSnapshot | null
): number {
  const base = Math.max(0, Math.floor(Number(stats?.total_study_time_seconds) || 0));
  const snap = snapshot;
  if (!snap || snap.phase !== "focus") {
    return base;
  }
  const full = Math.max(0, Math.floor(Number(snap.effectiveFocusMin) * 60));
  if (full <= 0) {
    return base;
  }
  const remaining = Math.max(0, Math.min(full, Math.floor(Number(snap.remainingSeconds) || 0)));
  const liveElapsed = Math.max(0, full - remaining);
  if (!snap.isRunning && liveElapsed <= 0) {
    return base;
  }
  return base + liveElapsed;
}

export function hasLiveFocusIncrementValue(snapshot: PomodoroTimerSnapshot | null): boolean {
  const snap = snapshot;
  if (!snap || snap.phase !== "focus") {
    return false;
  }
  const full = Math.max(0, Math.floor(Number(snap.effectiveFocusMin) * 60));
  const remaining = Math.max(0, Math.min(full, Math.floor(Number(snap.remainingSeconds) || 0)));
  return Math.max(0, full - remaining) > 0;
}

export function pomodoroTopicBreakdownRows(
  stats: StudyStats | null
): { topic: string; seconds: number; pct: number }[] {
  const map = stats?.pomodoro_by_topic_seconds;
  if (!map || typeof map !== "object") {
    return [];
  }
  const total = Object.values(map).reduce((a, b) => a + (Number(b) || 0), 0);
  if (total <= 0) {
    return [];
  }
  return Object.entries(map)
    .map(([topic, sec]) => ({
      topic,
      seconds: Math.max(0, Math.round(Number(sec) || 0)),
      pct: Math.min(100, Math.round((100 * (Number(sec) || 0)) / total)),
    }))
    .sort((a, b) => b.seconds - a.seconds);
}

export function quizLifecycleBarRows(
  stats: StudyStats | null
): { kind: "correct" | "wrong" | "blank"; pct: number }[] {
  const slots = stats?.quiz_sum_question_slots ?? 0;
  if (!stats || slots <= 0) {
    return [];
  }
  const c = stats.quiz_sum_correct ?? 0;
  const w = stats.quiz_sum_wrong ?? 0;
  const u = stats.quiz_sum_unanswered ?? 0;
  return [
    { kind: "correct", pct: (100 * c) / slots },
    { kind: "wrong", pct: (100 * w) / slots },
    { kind: "blank", pct: (100 * u) / slots },
  ];
}
