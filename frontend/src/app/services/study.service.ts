import { Injectable } from "@angular/core";
import { QuizHistoryListResponse, StudyStats } from "../models/types";
import { ApiService } from "./api.service";

/** Defaults so older API responses or partial payloads do not break the Progress UI. */
function normalizeStudyStats(raw: Partial<StudyStats>): StudyStats {
  return {
    user_id: raw.user_id ?? 0,
    total_study_time_seconds: raw.total_study_time_seconds ?? 0,
    pomodoro_sessions_count: raw.pomodoro_sessions_count ?? 0,
    total_quiz_count: raw.total_quiz_count ?? 0,
    average_quiz_score: raw.average_quiz_score ?? null,
    weak_topics: raw.weak_topics ?? [],
    strong_topics: raw.strong_topics ?? [],
    quiz_sum_correct: raw.quiz_sum_correct ?? 0,
    quiz_sum_wrong: raw.quiz_sum_wrong ?? 0,
    quiz_sum_unanswered: raw.quiz_sum_unanswered ?? 0,
    quiz_sum_question_slots: raw.quiz_sum_question_slots ?? 0,
    quiz_lifetime_accuracy_percent: raw.quiz_lifetime_accuracy_percent ?? null,
    pomodoro_by_topic_seconds: raw.pomodoro_by_topic_seconds ?? {},
    pomodoro_recent_sessions: raw.pomodoro_recent_sessions ?? [],
    calculation_proof: raw.calculation_proof ?? null,
  };
}

@Injectable({
  providedIn: "root",
})
export class StudyService {
  constructor(private api: ApiService) {}

  getStats(): Promise<StudyStats> {
    return this.api
      .getStudyStats()
      .toPromise()
      .then((s) => normalizeStudyStats((s || {}) as Partial<StudyStats>));
  }

  getQuizHistory(limit = 60): Promise<QuizHistoryListResponse> {
    return this.api.getQuizHistory(limit).toPromise().then((r) => r || { items: [] });
  }
}
