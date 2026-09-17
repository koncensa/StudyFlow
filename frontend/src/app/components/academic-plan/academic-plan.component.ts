// cmp: academic-plan | tr: akademik çalışma planı ekranı — plan oluştur, görevler, geçmiş / en: academic study plan screen create tasks history

import { Component, Input, OnInit } from "@angular/core";

import { AcademicPlanGenerateRequest, AcademicPlanHistoryItem, AcademicPlanTask, AcademicPlanView } from "../../models/types";
import { ApiService } from "../../services/api.service";
import { readApiError } from "../../utils/api-error";

@Component({
  selector: "app-academic-plan",
  templateUrl: "./academic-plan.component.html",
  styleUrls: ["./academic-plan.component.css"],
})
export class AcademicPlanComponent implements OnInit {
  @Input() locale: "en" | "tr" = "en";
  leftPanelTab: "goal" | "history" = "goal";

  plan: AcademicPlanView | null = null;
  loading = false;
  busy = false;
  error: string | null = null;
  historyLoading = false;
  historyError: string | null = null;
  historyItems: AcademicPlanHistoryItem[] = [];
  historyDetailsByPlanId: Record<number, AcademicPlanView | null | undefined> = {};
  expandedHistoryPlanId: number | null = null;
  historyViewLoading = false;
  historyViewLoadingPlanId: number | null = null;

  courseName = "";
  goalText = "";
  deadlineDate = "";
  dailyHours = 2;
  studyDays: number[] = [0, 1, 2, 3, 4];
  weakTopicsText = "";
  confidentTopicsText = "";
  outlineText = "";

  readonly weekdayDefs = [
    { v: 0, en: "Mon", tr: "Pzt" },
    { v: 1, en: "Tue", tr: "Sal" },
    { v: 2, en: "Wed", tr: "Çar" },
    { v: 3, en: "Thu", tr: "Per" },
    { v: 4, en: "Fri", tr: "Cum" },
    { v: 5, en: "Sat", tr: "Cmt" },
    { v: 6, en: "Sun", tr: "Paz" },
  ];

  constructor(private api: ApiService) {
    const d = new Date();
    d.setDate(d.getDate() + 14);
    this.deadlineDate = d.toISOString().slice(0, 10);
  }

  ngOnInit(): void {
    this.loadLatest();
    this.loadHistory();
  }

  /** Scroll to in-page section (visible “menu” targets). */
  jump(id: string): void {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  openLeftPanelTab(tab: "goal" | "history"): void {
    this.leftPanelTab = tab;
    this.jump("sf-ap-goal");
  }

  openPlanSummaryPanel(): void {
    if (this.leftPanelTab === "history") {
      this.leftPanelTab = "goal";
      setTimeout(() => this.jump("sf-ap-output"), 0);
      return;
    }
    this.jump("sf-ap-output");
  }

  clientTodayIso(): string {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  wdayLabel(v: number): string {
    const row = this.weekdayDefs.find((x) => x.v === v);
    if (!row) {
      return String(v);
    }
    return this.locale === "tr" ? row.tr : row.en;
  }

  toggleDay(v: number): void {
    const i = this.studyDays.indexOf(v);
    if (i >= 0) {
      if (this.studyDays.length <= 1) {
        return;
      }
      this.studyDays = this.studyDays.filter((d) => d !== v).sort((a, b) => a - b);
    } else {
      this.studyDays = [...this.studyDays, v].sort((a, b) => a - b);
    }
  }

  daySelected(v: number): boolean {
    return this.studyDays.includes(v);
  }

  splitTopics(raw: string): string[] {
    return raw
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  loadLatest(): void {
    this.loading = true;
    this.error = null;
    this.api.getAcademicPlanLatest(this.clientTodayIso()).subscribe({
      next: (res) => {
        this.plan = res.has_plan && res.plan ? res.plan : null;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.error = this.locale === "tr" ? "Plan yüklenemedi." : "Could not load plan.";
      },
    });
  }

  loadHistory(): void {
    this.historyLoading = true;
    this.historyError = null;
    this.api.getAcademicPlanHistory().subscribe({
      next: (res) => {
        this.historyItems = res.items || [];
        this.historyLoading = false;
      },
      error: () => {
        this.historyLoading = false;
        this.historyError = this.locale === "tr" ? "Plan geçmişi yüklenemedi." : "Could not load plan history.";
      },
    });
  }

  generate(): void {
    if (!this.courseName.trim() || !this.goalText.trim() || !this.deadlineDate) {
      this.error = this.locale === "tr" ? "Ders adı, hedef ve sınav tarihi zorunlu." : "Course name, goal, and deadline are required.";
      return;
    }
    const todayIso = this.clientTodayIso();
    if (this.deadlineDate < todayIso) {
      this.error =
        this.locale === "tr"
          ? "Sınav/deadline tarihi bugünden önce olamaz."
          : "Deadline date cannot be earlier than today.";
      return;
    }
    const body: AcademicPlanGenerateRequest = {
      course_name: this.courseName.trim(),
      goal_text: this.goalText.trim(),
      deadline_date: this.deadlineDate,
      daily_hours: Math.min(16, Math.max(0.25, Number(this.dailyHours))),
      study_days: [...this.studyDays],
      weak_topics: this.splitTopics(this.weakTopicsText),
      confident_topics: this.splitTopics(this.confidentTopicsText),
      topic_outline: this.outlineText
        .split(/\n+/)
        .map((s) => s.trim())
        .filter(Boolean),
      locale: this.locale,
    };
    this.busy = true;
    this.error = null;
    this.api.generateAcademicPlan(body, this.clientTodayIso()).subscribe({
      next: (p) => {
        this.plan = p;
        this.loadHistory();
        this.busy = false;
      },
      error: (err: unknown) => {
        this.busy = false;
        this.error = readApiError(err, this.locale === "tr" ? "Plan oluşturulamadı." : "Could not create plan.");
      },
    });
  }

  setTaskStatus(t: AcademicPlanTask, status: "pending" | "completed" | "missed"): void {
    if (!this.plan || this.plan.is_finished) {
      return;
    }
    this.busy = true;
    this.api.patchAcademicPlanTask(this.plan.plan_id, t.task_id, status, this.clientTodayIso()).subscribe({
      next: (p) => {
        this.plan = p;
        this.busy = false;
      },
      error: () => {
        this.busy = false;
      },
    });
  }

  hasCompletableTasks(): boolean {
    return !!this.plan?.tasks?.some((t) => t.status !== "completed") && !this.plan?.is_finished;
  }

  hasCancelableTasks(): boolean {
    return !!this.plan?.tasks?.some((t) => t.status !== "pending") && !this.plan?.is_finished;
  }

  canFinishPlan(): boolean {
    if (!this.plan || this.plan.is_finished || !this.plan.tasks?.length) {
      return false;
    }
    return this.plan.tasks.every((t) => t.status !== "pending");
  }

  finishPlan(): void {
    if (!this.plan || !this.canFinishPlan()) {
      return;
    }
    this.busy = true;
    this.error = null;
    const planId = this.plan.plan_id;
    this.api.finishAcademicPlan(planId, this.clientTodayIso()).subscribe({
      next: () => {
        this.plan = null;
        this.busy = false;
        this.loadHistory();
      },
      error: (err: unknown) => {
        this.busy = false;
        this.error = readApiError(err, this.locale === "tr" ? "Plan tamamlanamadı." : "Could not finish the plan.");
      },
    });
  }

  markAllDone(): void {
    this.markAllByStatus("completed");
  }

  cancelAllDone(): void {
    this.markAllByStatus("pending");
  }

  private markAllByStatus(status: "pending" | "completed"): void {
    if (!this.plan || this.busy) {
      return;
    }
    const tasksToUpdate = this.plan.tasks.filter((t) => t.status !== status);
    if (!tasksToUpdate.length) {
      return;
    }
    this.busy = true;
    this.error = null;
    this.markAllDoneStep(tasksToUpdate, status, 0);
  }

  private markAllDoneStep(tasks: AcademicPlanTask[], status: "pending" | "completed", index: number): void {
    if (!this.plan) {
      this.busy = false;
      return;
    }
    if (index >= tasks.length) {
      this.busy = false;
      return;
    }
    const t = tasks[index];
    this.api.patchAcademicPlanTask(this.plan.plan_id, t.task_id, status, this.clientTodayIso()).subscribe({
      next: (p) => {
        this.plan = p;
        this.markAllDoneStep(tasks, status, index + 1);
      },
      error: () => {
        this.busy = false;
        this.error =
          status === "completed"
            ? this.locale === "tr"
              ? "Tüm görevler tamamlanamadı."
              : "Could not mark all tasks as done."
            : this.locale === "tr"
              ? "Tüm görevler beklemeye alınamadı."
              : "Could not reset all tasks to pending.";
      },
    });
  }

  /** Remove this plan from the server so the left-hand form can create a fresh one. */
  deleteEntirePlan(): void {
    if (!this.plan) {
      return;
    }
    const ok = window.confirm(
      this.locale === "tr"
        ? "Bu akademik plan tamamen silinsin mi? Yenisini soldaki formdan oluşturabilirsiniz."
        : "Delete this academic plan entirely? You can build a new one from the form on the left."
    );
    if (!ok) {
      return;
    }
    const id = this.plan.plan_id;
    this.busy = true;
    this.error = null;
    this.api.deleteAcademicPlan(id).subscribe({
      next: () => {
        this.plan = null;
        this.busy = false;
        this.loadHistory();
      },
      error: (err: unknown) => {
        this.busy = false;
        this.error = readApiError(err, this.locale === "tr" ? "Plan silinemedi." : "Could not delete the plan.");
      },
    });
  }

  /** First 10 chars of an API date string (YYYY-MM-DD). */
  planDateKey(iso: string | null | undefined): string {
    const s = String(iso || "").trim();
    return s.length >= 10 ? s.slice(0, 10) : s;
  }

  /** True when the highlighted task is on a different calendar day than `today_iso` (off-day or today already done). */
  todayCardShowsNextStudyDay(): boolean {
    const p = this.plan;
    if (!p?.today_recommendation) {
      return false;
    }
    return this.planDateKey(p.today_recommendation.date_iso) !== this.planDateKey(p.today_iso);
  }

  todayNextStudyDateLabel(): string {
    const rec = this.plan?.today_recommendation;
    return rec ? this.planDateKey(rec.date_iso) : "";
  }

  taskCounts(): { completed: number; pending: number; missed: number } {
    if (!this.plan) {
      return { completed: 0, pending: 0, missed: 0 };
    }
    let completed = 0;
    let pending = 0;
    let missed = 0;
    for (const t of this.plan.tasks) {
      if (t.status === "completed") {
        completed++;
      } else if (t.status === "missed") {
        missed++;
      } else {
        pending++;
      }
    }
    return { completed, pending, missed };
  }

  completionPercent(): number {
    if (!this.plan?.tasks?.length) {
      return 0;
    }
    const counts = this.taskCounts();
    const total = this.plan.tasks.length;
    return Math.max(0, Math.min(100, Math.round((counts.completed / total) * 100)));
  }

  historyProgressText(item: AcademicPlanHistoryItem): string {
    const total = Math.max(0, Number(item.total_task_count || 0));
    const completed = Math.max(0, Number(item.completed_count || 0));
    const missed = Math.max(0, Number(item.missed_count || 0));
    return `${completed}/${total} · ${missed} ${this.locale === "tr" ? "kaçırılan" : "missed"}`;
  }

  isLatestHistoryItem(item: AcademicPlanHistoryItem, idx: number): boolean {
    if (!item) {
      return false;
    }
    if (idx === 0) {
      return true;
    }
    return this.historyItems.length > 0 && item.plan_id === this.historyItems[0].plan_id;
  }

  isHistoryExpanded(item: AcademicPlanHistoryItem): boolean {
    return !!item?.plan_id && this.expandedHistoryPlanId === item.plan_id;
  }

  toggleHistoryDetails(item: AcademicPlanHistoryItem): void {
    if (!item?.plan_id) {
      return;
    }
    if (this.expandedHistoryPlanId === item.plan_id) {
      this.expandedHistoryPlanId = null;
      return;
    }
    this.expandedHistoryPlanId = item.plan_id;
    const cached = this.historyDetailsByPlanId[item.plan_id];
    if (cached) {
      return;
    }
    this.historyViewLoading = true;
    this.historyViewLoadingPlanId = item.plan_id;
    this.api.getAcademicPlanHistoryItem(item.plan_id, this.clientTodayIso()).subscribe({
      next: (view) => {
        this.historyDetailsByPlanId[item.plan_id] = view;
        this.historyViewLoading = false;
        this.historyViewLoadingPlanId = null;
      },
      error: () => {
        this.historyDetailsByPlanId[item.plan_id] = null;
        this.historyViewLoading = false;
        this.historyViewLoadingPlanId = null;
      },
    });
  }

  isFallingBehind(): boolean {
    const counts = this.taskCounts();
    return counts.missed > counts.completed;
  }

  progressMessage(): string {
    const counts = this.taskCounts();
    if (counts.completed >= Math.max(3, counts.pending) && !this.isFallingBehind()) {
      return this.locale === "tr" ? "Harika gidiyorsun! Aynen devam et." : "Great job! Keep going.";
    }
    if (this.isFallingBehind()) {
      return this.locale === "tr"
        ? "Geride kalıyorsun. Günlük çalışma süreni artırmayı düşünebilirsin."
        : "You are falling behind. Consider increasing daily study time.";
    }
    return this.locale === "tr" ? "Plan genel olarak yolunda." : "You're on track.";
  }

  progressToneClass(): string {
    if (this.isFallingBehind()) {
      return "sf-progressNote sf-progressNote--warn";
    }
    if (this.completionPercent() >= 70) {
      return "sf-progressNote sf-progressNote--good";
    }
    return "sf-progressNote sf-progressNote--neutral";
  }

  taskCardClass(status: "pending" | "completed" | "missed"): string {
    if (status === "completed") {
      return "sf-taskCard sf-taskCard--completed";
    }
    if (status === "missed") {
      return "sf-taskCard sf-taskCard--missed";
    }
    return "sf-taskCard";
  }

  kindClass(kind: string): string {
    switch (kind) {
      case "review":
        return "badge bg-info text-dark";
      case "quiz":
        return "badge bg-warning text-dark";
      case "buffer":
        return "badge bg-secondary";
      default:
        return "badge bg-primary";
    }
  }

  kindPillClass(kind: string): string {
    switch (kind) {
      case "review":
        return "sf-kindPill sf-kindPill--review";
      case "quiz":
        return "sf-kindPill sf-kindPill--quiz";
      case "buffer":
        return "sf-kindPill sf-kindPill--rest";
      default:
        return "sf-kindPill sf-kindPill--study";
    }
  }

  kindLabel(kind: string): string {
    if (this.locale === "tr") {
      switch (kind) {
        case "study":
          return "Çalışma";
        case "review":
          return "Tekrar";
        case "quiz":
          return "Quiz";
        case "buffer":
          return "Dinlenme";
        default:
          return kind;
      }
    }
    switch (kind) {
      case "study":
        return "Study";
      case "review":
        return "Review";
      case "quiz":
        return "Quiz";
      case "buffer":
        return "Rest";
      default:
        return kind;
    }
  }
}
