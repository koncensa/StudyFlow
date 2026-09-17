// cmp: study-analysis | tr: ilerleme/stats paneli — pomodoro + quiz istatistikleri / en: progress stats panel pomodoro and quiz metrics

import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from "@angular/core";
import { take } from "rxjs/operators";

import { AppLang } from "../../i18n/app-lang";
import {
  BestResourcesResponse,
  LastQuizSummary,
  QuizCoachingPack,
  QuizQuestionFeedback,
  QuizSubmissionResponse,
  StudyResourceTip,
  SuggestionResponse,
  TailoredMiniQuizPayload,
  TopicAnalysisResponse,
  TopicPerformance,
  TopicProgressItem,
} from "../../models/types";
import { ApiService } from "../../services/api.service";
import { readApiError } from "../../utils/api-error";
import { quizScorePercentFromAttempt } from "../../utils/quiz-score-display";
import { feedbackRowIsCorrect, feedbackRowIsUnanswered } from "../../utils/quiz-submission-normalize";

interface AnalysisHistoryItem {
  id?: string;
  solvedAtIso: string;
  score: number;
  correct: number;
  wrong: number;
  unanswered: number;
  quizSource?: string;
  focusTopics: string[];
  recommendationLines: string[];
  topicRows?: {
    topic: string;
    correct: number;
    total: number;
    pct: number;
    status: string;
    wrong: number;
  }[];
  errorSummary?: Record<string, number>;
}

/** Normalized per-topic row for the analysis table (counts + 0–100% rate). */
interface TopicMetricRow {
  topic: string;
  correct: number;
  total: number;
  wrong: number;
  pct: number;
  status: string;
}

@Component({
  selector: "app-study-analysis",
  templateUrl: "./study-analysis.component.html",
  styleUrls: ["./study-analysis.component.css"],
})
export class StudyAnalysisComponent implements OnChanges {
  constructor(private api: ApiService) {}

  @Input() topicAnalysis: TopicAnalysisResponse | null = null;
  @Input() quizSubmission: QuizSubmissionResponse | null = null;
  @Input() suggestions: SuggestionResponse | null = null;
  @Input() topicProgress: TopicProgressItem[] = [];
  @Input() lastQuizSummary: LastQuizSummary | null = null;
  @Input() resultsLoading = false;
  @Input() resultsError: string | null = null;
  @Input() locale: AppLang = "en";
  @Input() hasActivePdf = false;
  @Input() analysisHistory: AnalysisHistoryItem[] = [];

  @Output() retryLoadResults = new EventEmitter<void>();
  @Output() studyTopic = new EventEmitter<string | null>();
  @Output() openAssistant = new EventEmitter<void>();
  @Output() openStatsTab = new EventEmitter<void>();
  @Output() startTailoredMiniQuiz = new EventEmitter<TailoredMiniQuizPayload>();
  @Output() startCoachingQuiz = new EventEmitter<void>();
  selectedHistoryId: string | null = null;
  analysisScope: "selected" | "all" = "selected";
  analysisView: "focus" | "detailed" = "focus";
  historySourceFilter: "all" | "pdf_session" | "text_summary" | "other" = "all";
  selectedFocusTopic: string | null = null;

  /** Sunucunun seçtiği video + web (GET /study/best-resources). */
  curatedResources: BestResourcesResponse | null = null;
  curatedLoading = false;
  curatedError: string | null = null;
  private curatedFetchKey = "";
  private curatedFetchInFlight = false;

  get coachingPack(): QuizCoachingPack | null {
    const s = this.suggestions;
    return s && s.coaching_pack ? s.coaching_pack : null;
  }

  get hasShell(): boolean {
    const fb = this.quizSubmission?.question_feedback?.length ?? 0;
    return !!(this.lastQuizSummary || this.quizSubmission || (this.topicRows.length > 0) || fb > 0);
  }

  get showLoader(): boolean {
    return this.resultsLoading && !this.lastQuizSummary && !this.quizSubmission;
  }

  get topicRows(): TopicPerformance[] {
    const ta = this.topicAnalysis;
    return ta && ta.topics && ta.topics.length ? ta.topics : [];
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes["analysisHistory"]) {
      const rows = this.analysisHistoryRows();
      if (!rows.length) {
        this.selectedHistoryId = null;
        this.selectedFocusTopic = null;
        return;
      }
      const hasCurrent = rows.some((r) => this.historyRowId(r) === this.selectedHistoryId);
      if (!hasCurrent) {
        this.selectedHistoryId = this.historyRowId(rows[0]);
      }
      const topics = this.focusTopicCandidates();
      if (!topics.length) {
        this.selectedFocusTopic = null;
      } else if (!this.selectedFocusTopic || !topics.includes(this.selectedFocusTopic)) {
        this.selectedFocusTopic = topics[0];
      }
    }

    if (this.resultsLoading) {
      return;
    }
    const finishedResultsLoad =
      changes["resultsLoading"]?.previousValue === true && changes["resultsLoading"]?.currentValue === false;
    const subChange = changes["quizSubmission"];
    const submissionMeaningfullyChanged =
      !!subChange &&
      (subChange.firstChange ||
        (subChange.previousValue as QuizSubmissionResponse | null)?.attempt_id !==
          (subChange.currentValue as QuizSubmissionResponse | null)?.attempt_id ||
        (subChange.previousValue as QuizSubmissionResponse | null)?.score_percentage !==
          (subChange.currentValue as QuizSubmissionResponse | null)?.score_percentage);
    const shouldLoadCurated =
      finishedResultsLoad ||
      submissionMeaningfullyChanged ||
      !!changes["suggestions"] ||
      !!changes["topicAnalysis"] ||
      !!changes["locale"] ||
      !!changes["analysisHistory"];
    if (shouldLoadCurated) {
      this.loadCuratedResources();
    }
  }

  historyRowId(row: AnalysisHistoryItem): string {
    const base = String(row.id || "").trim();
    if (base) {
      return base;
    }
    return `${String(row.solvedAtIso || "").trim()}-${Math.round(Number(row.score) || 0)}`;
  }

  selectHistoryRow(row: AnalysisHistoryItem): void {
    this.selectedHistoryId = this.historyRowId(row);
    const topics = this.focusTopicCandidates();
    if (topics.length && (!this.selectedFocusTopic || !topics.includes(this.selectedFocusTopic))) {
      this.selectedFocusTopic = topics[0];
    }
    this.loadCuratedResources();
  }

  isSelectedHistoryRow(row: AnalysisHistoryItem): boolean {
    return this.selectedHistoryId === this.historyRowId(row);
  }

  selectedHistoryRow(): AnalysisHistoryItem | null {
    const rows = this.analysisHistoryRows();
    if (!rows.length) {
      return null;
    }
    const picked = rows.find((r) => this.historyRowId(r) === this.selectedHistoryId);
    return picked || rows[0];
  }

  setHistorySourceFilter(v: "all" | "pdf_session" | "text_summary" | "other"): void {
    this.historySourceFilter = v;
    const rows = this.analysisHistoryRows();
    if (!rows.length) {
      this.selectedHistoryId = null;
      return;
    }
    const hasCurrent = rows.some((r) => this.historyRowId(r) === this.selectedHistoryId);
    if (!hasCurrent) {
      this.selectedHistoryId = this.historyRowId(rows[0]);
    }
  }

  sourceFilterOptions(): { key: "all" | "pdf_session" | "text_summary" | "other"; label: string; count: number }[] {
    const raw = this.rawAnalysisHistoryRows();
    const pdf = raw.filter((r) => (r.quizSource || "other") === "pdf_session").length;
    const txt = raw.filter((r) => (r.quizSource || "other") === "text_summary").length;
    const oth = raw.filter((r) => {
      const s = r.quizSource || "other";
      return s !== "pdf_session" && s !== "text_summary";
    }).length;
    return [
      { key: "all", label: this.t("All", "Tümü"), count: raw.length },
      { key: "pdf_session", label: this.t("PDF session", "PDF oturumu"), count: pdf },
      { key: "text_summary", label: this.t("Text summary", "Metin özeti"), count: txt },
      { key: "other", label: this.t("Other", "Diğer"), count: oth },
    ];
  }

  activeAttemptScore(): number {
    const picked = this.selectedHistoryRow();
    if (picked) {
      return Math.max(0, Math.round(Number(picked.score) || 0));
    }
    return Math.max(0, Math.round(Number(this.lastQuizSummary?.score ?? 0)));
  }

  activeAttemptCorrect(): number {
    const picked = this.selectedHistoryRow();
    if (picked) {
      return Math.max(0, Math.round(Number(picked.correct) || 0));
    }
    return Math.max(0, Math.round(Number(this.lastQuizSummary?.correct ?? 0)));
  }

  activeAttemptWrong(): number {
    const picked = this.selectedHistoryRow();
    if (picked) {
      return Math.max(0, Math.round(Number(picked.wrong) || 0));
    }
    return Math.max(0, Math.round(Number(this.lastQuizSummary?.wrong ?? 0)));
  }

  activeAttemptBlank(): number {
    const picked = this.selectedHistoryRow();
    if (picked) {
      return Math.max(0, Math.round(Number(picked.unanswered) || 0));
    }
    return Math.max(0, Math.round(Number(this.lastQuizSummary?.unanswered ?? 0)));
  }

  activeAttemptTotal(): number {
    return this.activeAttemptCorrect() + this.activeAttemptWrong() + this.activeAttemptBlank();
  }

  /** Üst başlık altında tek satır özet — uzun paragraflardan kaçınır. */
  headerSubtitle(): string {
    const tr = this.locale === "tr";
    const score = this.activeAttemptScore();
    const weak = this.weakestTopicName().trim();
    const tot = this.activeAttemptTotal();
    if (this.analysisScope === "all") {
      const n = this.analysisHistoryRows().length;
      if (!n) {
        return tr ? "Henüz yeterli deneme yok." : "Not enough attempts yet.";
      }
      return tr
        ? `${n} denemenin birleşik özeti${weak ? ` · en zor alan: ${weak}` : ""}.`
        : `Combined view of ${n} attempt(s)${weak ? ` · hardest area: ${weak}` : ""}.`;
    }
    if (!tot) {
      return tr ? "Quiz sonucu yüklendiğinde burada görünecek." : "Your latest attempt summary will appear here.";
    }
    return tr
      ? `${tot} soru · skor %${score}${weak ? ` · öncelik: ${weak}` : ""}.`
      : `${tot} questions · score ${score}%${weak ? ` · priority: ${weak}` : ""}.`;
  }

  topicMetricRows(): TopicMetricRow[] {
    if (this.analysisScope === "all") {
      const fromAll = this.aggregateTopicRowsFromHistory();
      if (fromAll.length) {
        return fromAll;
      }
    }
    const picked = this.selectedHistoryRow();
    const fromHistory = (picked?.topicRows || [])
      .map((r) => ({
        topic: String(r.topic || "").trim(),
        correct: Math.max(0, Math.round(Number(r.correct) || 0)),
        total: Math.max(0, Math.round(Number(r.total) || 0)),
        wrong: Math.max(0, Math.round(Number(r.wrong) || 0)),
        ...this.computePctAndStatusFromCounts(
          Math.max(0, Math.round(Number(r.correct) || 0)),
          Math.max(0, Math.round(Number(r.total) || 0))
        ),
      }))
      .filter((r) => r.topic.length > 0 && r.total > 0);
    if (fromHistory.length) {
      return fromHistory;
    }
    const fromApi = this.topicMetricRowsFromTopicPerformance(this.topicRows);
    if (fromApi.length) {
      return fromApi;
    }
    const fb = this.quizSubmission?.question_feedback;
    if (fb && fb.length) {
      const derived = this.topicMetricRowsFromFeedback(fb);
      if (derived.length) {
        return derived;
      }
    }
    return [];
  }

  /** Topics with success rate below 60% (same scoring as topic highlights). */
  weakTopicMetricRows(): TopicMetricRow[] {
    return this.topicMetricRows()
      .filter((r) => r.pct < 60)
      .sort((a, b) => a.pct - b.pct);
  }

  /** Topics with success rate 75% and above. */
  strongTopicMetricRows(): TopicMetricRow[] {
    return this.topicMetricRows()
      .filter((r) => r.pct >= 75)
      .sort((a, b) => b.pct - a.pct);
  }

  /** Mid band 60–74% (shown under weak/strong detail). */
  developingTopicMetricRows(): TopicMetricRow[] {
    return this.topicMetricRows()
      .filter((r) => r.pct >= 60 && r.pct < 75)
      .sort((a, b) => a.pct - b.pct);
  }

  hasTopicBandSection(): boolean {
    return (
      this.weakTopicMetricRows().length > 0 ||
      this.strongTopicMetricRows().length > 0 ||
      this.developingTopicMetricRows().length > 0
    );
  }

  /** One-decimal percent for display (table); bar width uses the same numeric pct. */
  formatTopicRatePct(row: TopicMetricRow): string {
    const v = Number(row.pct);
    if (!Number.isFinite(v)) {
      return "0";
    }
    return (Math.round(v * 10) / 10).toFixed(1);
  }

  barWidthPct(pct: number): number {
    return Math.max(0, Math.min(100, Number(pct) || 0));
  }

  attemptStudyNote(): string {
    const tr = this.locale === "tr";
    const lines: string[] = [];
    const score = this.resolvedScorePercent();
    const tot = this.activeAttemptTotal();
    if (tot > 0) {
      lines.push(
        tr
          ? `Deneme: ${tot} soru · skor %${score} (doğru ${this.activeAttemptCorrect()}, yanlış ${this.activeAttemptWrong()}, boş ${this.activeAttemptBlank()}).`
          : `Attempt: ${tot} questions · score ${score}% (correct ${this.activeAttemptCorrect()}, wrong ${this.activeAttemptWrong()}, blank ${this.activeAttemptBlank()}).`
      );
    }
    const rows = this.topicMetricRows();
    if (rows.length) {
      lines.push(tr ? "Konu bazında (doğru / toplam · %):" : "By topic (correct / total · %):");
      for (const r of rows) {
        lines.push(`- ${r.topic}: ${r.correct}/${r.total} · ${this.formatTopicRatePct(r)}%`);
      }
    }
    const weak = this.weakTopicRows();
    const dev = this.developingTopicRows();
    const strong = this.strongTopicRows();
    if (weak.length) {
      lines.push(
        tr
          ? `Öncelik (%60 altı): ${weak.map((w) => w.topic).join(", ")}.`
          : `Priority (below 60%): ${weak.map((w) => w.topic).join(", ")}.`
      );
    }
    if (dev.length) {
      lines.push(
        tr
          ? `Orta band (60–75%): ${dev.map((w) => w.topic).join(", ")}.`
          : `Mid band (60–75%): ${dev.map((w) => w.topic).join(", ")}.`
      );
    }
    if (strong.length) {
      lines.push(
        tr
          ? `Güçlü alanlar (≥%75): ${strong.map((w) => w.topic).join(", ")}.`
          : `Solid areas (≥75%): ${strong.map((w) => w.topic).join(", ")}.`
      );
    }
    return lines.join("\n").trim();
  }

  copyStudyNote(el: HTMLTextAreaElement | null): void {
    const text = this.attemptStudyNote().trim();
    if (!text || !el) {
      return;
    }
    el.focus();
    el.select();
    void navigator.clipboard?.writeText?.(text);
  }

  private aggregateTopicRowsFromHistory(): TopicMetricRow[] {
    const rows = this.analysisHistoryRows();
    const map = new Map<string, { topic: string; correct: number; total: number; wrong: number }>();
    for (const row of rows) {
      for (const tr of row.topicRows || []) {
        const topic = String(tr.topic || "").trim();
        if (!topic) {
          continue;
        }
        const cur = map.get(topic) || { topic, correct: 0, total: 0, wrong: 0 };
        cur.correct += Math.max(0, Math.round(Number(tr.correct) || 0));
        cur.total += Math.max(0, Math.round(Number(tr.total) || 0));
        cur.wrong += Math.max(0, Math.round(Number(tr.wrong) || 0));
        map.set(topic, cur);
      }
    }
    return [...map.values()]
      .map((x) => {
        const ps = this.computePctAndStatusFromCounts(x.correct, x.total);
        return {
          topic: x.topic,
          correct: x.correct,
          total: x.total,
          wrong: x.wrong,
          pct: ps.pct,
          status: ps.status,
        };
      })
      .filter((x) => x.total > 0)
      .sort((a, b) => a.pct - b.pct);
  }

  private computePctAndStatusFromCounts(correct: number, total: number): Pick<TopicMetricRow, "pct" | "status"> {
    const c = Math.max(0, Math.round(Number(correct) || 0));
    const t = Math.max(0, Math.round(Number(total) || 0));
    const pctRaw = t > 0 ? (c / t) * 100 : 0;
    const pct = Math.round(pctRaw * 10) / 10;
    return { pct, status: this.metricStatusClass(pct) };
  }

  private topicMetricRowsFromTopicPerformance(topics: TopicPerformance[]): TopicMetricRow[] {
    return topics
      .map((r) => {
        const correct = Math.max(0, Math.round(Number(r.correct_count) || 0));
        const total = Math.max(0, Math.round(Number(r.total_attempts) || 0));
        let wrong = Math.max(0, Math.round(Number(r.wrong_count ?? 0)));
        const srRaw = Number(r.success_rate);
        const sr01 = Number.isFinite(srRaw) && srRaw > 1 ? srRaw / 100 : Number(srRaw) || 0;
        let pct = total > 0 ? Math.round((correct / total) * 1000) / 10 : 0;
        if (total > 0 && Number.isFinite(srRaw)) {
          const fromSr = Math.round(sr01 * 1000) / 10;
          const fromCounts = Math.round((correct / total) * 1000) / 10;
          pct = Math.abs(fromSr - fromCounts) > 12 ? fromCounts : fromSr;
        }
        if (wrong === 0 && total > 0) {
          wrong = Math.max(0, total - correct);
        }
        return {
          topic: String(r.topic || "").trim() || "—",
          correct,
          total,
          wrong,
          pct,
          status: this.metricStatusClass(pct),
        };
      })
      .filter((x) => x.topic.length > 0 && x.topic !== "—" && x.total > 0);
  }

  private topicMetricRowsFromFeedback(fb: QuizQuestionFeedback[]): TopicMetricRow[] {
    const general = this.locale === "tr" ? "Genel" : "General";
    const map = new Map<string, { c: number; w: number; t: number }>();
    for (const row of fb) {
      const topicRaw = (row.topic || "").trim();
      const topic = topicRaw.length ? topicRaw : general;
      if (!map.has(topic)) {
        map.set(topic, { c: 0, w: 0, t: 0 });
      }
      const b = map.get(topic)!;
      b.t += 1;
      if (feedbackRowIsCorrect(row)) {
        b.c += 1;
      } else if (!feedbackRowIsUnanswered(row)) {
        b.w += 1;
      }
    }
    return [...map.entries()]
      .map(([topic, b]) => {
        const ps = this.computePctAndStatusFromCounts(b.c, b.t);
        return {
          topic,
          correct: b.c,
          total: b.t,
          wrong: b.w,
          pct: ps.pct,
          status: ps.status,
        };
      })
      .sort((a, b) => a.pct - b.pct);
  }

  topicPct(row: TopicPerformance): number {
    const r = Number(row.success_rate) || 0;
    const sr01 = r > 1 ? r / 100 : r;
    return Math.round(sr01 * 1000) / 10;
  }

  topicRateWidth(row: TopicPerformance): string {
    const pct = this.topicPct(row);
    return `${Math.max(0, Math.min(100, pct))}%`;
  }

  topicStatusClass(row: TopicPerformance): string {
    return this.metricStatusClass(this.topicPct(row));
  }

  /** Bar colours: align with server bands — solid ≥75%, building 60–74%, needs work &lt;60%. */
  metricStatusClass(pct: number): string {
    const p = Number(pct) || 0;
    if (p >= 75) {
      return "is-strong";
    }
    if (p >= 60) {
      return "is-mid";
    }
    return "is-weak";
  }

  weakTopicRows(): TopicPerformance[] {
    const pseudo = this.topicMetricRows()
      .filter((r) => r.pct < 60)
      .sort((a, b) => a.pct - b.pct)
      .map((r) => ({
        topic: r.topic,
        success_rate: r.pct / 100,
        correct_count: r.correct,
        total_attempts: r.total,
        wrong_count: r.wrong,
        status: r.status,
      } as TopicPerformance));
    return pseudo;
  }

  developingTopicRows(): TopicPerformance[] {
    const pseudo = this.topicMetricRows()
      .filter((r) => r.pct >= 60 && r.pct < 75)
      .sort((a, b) => a.pct - b.pct)
      .map((r) => ({
        topic: r.topic,
        success_rate: r.pct / 100,
        correct_count: r.correct,
        total_attempts: r.total,
        wrong_count: r.wrong,
        status: r.status,
      } as TopicPerformance));
    return pseudo;
  }

  strongTopicRows(): TopicPerformance[] {
    const pseudo = this.topicMetricRows()
      .filter((r) => r.pct >= 75)
      .sort((a, b) => b.pct - a.pct)
      .map((r) => ({
        topic: r.topic,
        success_rate: r.pct / 100,
        correct_count: r.correct,
        total_attempts: r.total,
        wrong_count: r.wrong,
        status: r.status,
      } as TopicPerformance));
    return pseudo;
  }

  errorEntries(): { key: string; count: number }[] {
    if (this.analysisScope === "all") {
      const fromAll = this.aggregateErrorSummaryFromHistory();
      if (fromAll.length) {
        return fromAll;
      }
    }
    const picked = this.selectedHistoryRow();
    const fromHistory = picked?.errorSummary;
    const s = fromHistory && Object.keys(fromHistory).length
      ? fromHistory
      : (this.quizSubmission && this.quizSubmission.error_type_summary);
    if (!s) {
      return [];
    }
    return Object.keys(s)
      .map((k) => ({ key: k, count: s[k] }))
      .sort((a, b) => b.count - a.count);
  }

  private aggregateErrorSummaryFromHistory(): { key: string; count: number }[] {
    const rows = this.analysisHistoryRows();
    const map = new Map<string, number>();
    for (const row of rows) {
      for (const k of Object.keys(row.errorSummary || {})) {
        const n = Math.max(0, Math.round(Number((row.errorSummary || {})[k] || 0)));
        if (!n) {
          continue;
        }
        map.set(k, (map.get(k) || 0) + n);
      }
    }
    return [...map.entries()]
      .map(([key, count]) => ({ key, count }))
      .sort((a, b) => b.count - a.count);
  }

  errorTotalCount(): number {
    return this.errorEntries().reduce((acc, row) => acc + Math.max(0, Number(row.count) || 0), 0);
  }

  errorPct(count: number): number {
    const total = this.errorTotalCount();
    if (!total) {
      return 0;
    }
    return Math.round((Math.max(0, Number(count) || 0) / total) * 100);
  }

  errorLabel(code: string): string {
    if (this.locale === "tr") {
      const map: Record<string, string> = {
        concept_mixup: "Kavram karışması",
        formula_mixup: "Formül / hesap",
        careless: "Dikkat / okuma",
        interpretation: "Yorumlama",
        unanswered: "Boş",
      };
      return map[code] || code;
    }
    const map: Record<string, string> = {
      concept_mixup: "Concept mix-up",
      formula_mixup: "Formula / calculation",
      careless: "Careless reading",
      interpretation: "Interpretation",
      unanswered: "Unanswered",
    };
    return map[code] || code;
  }

  weakestTopicName(): string {
    const rows = this.topicMetricRows();
    if (!rows.length) {
      return "";
    }
    const worst = [...rows].sort((a, b) => (a.pct || 100) - (b.pct || 100))[0];
    return (worst && worst.topic) || "";
  }

  strongestTopicName(): string {
    const rows = this.topicMetricRows();
    if (!rows.length) {
      return "";
    }
    const best = [...rows].sort((a, b) => (b.pct || 0) - (a.pct || 0))[0];
    return (best && best.topic) || "";
  }

  mostMissedTopics(): { topic: string; wrong: number }[] {
    const picked = this.selectedHistoryRow();
    if (picked?.topicRows?.length) {
      return [...picked.topicRows]
        .map((r) => ({ topic: String(r.topic || "").trim() || "—", wrong: Math.max(0, Math.round(Number(r.wrong) || 0)) }))
        .filter((r) => r.wrong > 0)
        .sort((a, b) => b.wrong - a.wrong);
    }
    const m = new Map<string, number>();
    const fb = this.quizSubmission && this.quizSubmission.question_feedback;
    if (fb && fb.length) {
      for (const q of fb) {
        if (feedbackRowIsCorrect(q) || feedbackRowIsUnanswered(q)) {
          continue;
        }
        const t = (q.topic || "").trim() || "—";
        m.set(t, (m.get(t) || 0) + 1);
      }
    } else {
      for (const r of this.topicRows) {
        const w = r.wrong_count || 0;
        if (w > 0) {
          m.set(r.topic || "—", w);
        }
      }
    }
    return [...m.entries()]
      .map(([topic, wrong]) => ({ topic, wrong }))
      .sort((a, b) => b.wrong - a.wrong);
  }

  t(en: string, tr: string): string {
    return this.locale === "tr" ? tr : en;
  }

  setAnalysisScope(scope: "selected" | "all"): void {
    this.analysisScope = scope;
    this.loadCuratedResources();
  }

  analysisScopeLabel(): string {
    if (this.analysisScope === "all") {
      return this.t("All quizzes (all PDFs)", "Tüm quizler (tüm PDF'ler)");
    }
    return this.t("Selected quiz only", "Sadece seçili quiz");
  }

  setAnalysisView(view: "focus" | "detailed"): void {
    this.analysisView = view;
  }

  showAdvancedSections(): boolean {
    return this.analysisView === "detailed";
  }

  focusTopicCandidates(): string[] {
    const out: string[] = [];
    const push = (v: string | null | undefined) => {
      const x = String(v || "").trim();
      if (!x || x === "—") {
        return;
      }
      if (!out.some((k) => k.toLowerCase() === x.toLowerCase())) {
        out.push(x);
      }
    };
    for (const w of this.weakTopicRows().slice(0, 5)) {
      push(w.topic);
    }
    for (const m of this.mostMissedTopics().slice(0, 5)) {
      push(m.topic);
    }
    for (const h of this.historyTopFocusTopics(5)) {
      push(h);
    }
    return out.slice(0, 8);
  }

  setFocusTopic(topic: string): void {
    this.selectedFocusTopic = topic;
    this.loadCuratedResources();
  }

  isFocusTopic(topic: string): boolean {
    return String(this.selectedFocusTopic || "").toLowerCase() === String(topic || "").toLowerCase();
  }

  emitMiniFromPack(): void {
    this.emitPersonalizedMiniQuiz();
  }

  /** Zayıf konular + skor bandı zorluğu + güçlü konulardan çapraz etiket (soru çeşitliliği). */
  emitPersonalizedMiniQuiz(): void {
    const plan = this.buildMiniQuizPayload();
    this.startTailoredMiniQuiz.emit(plan);
  }

  analysisHistoryRows(): AnalysisHistoryItem[] {
    const rows = this.rawAnalysisHistoryRows();
    if (this.historySourceFilter === "all") {
      return rows;
    }
    return rows.filter((r) => {
      const src = r.quizSource || "other";
      if (this.historySourceFilter === "other") {
        return src !== "pdf_session" && src !== "text_summary";
      }
      return src === this.historySourceFilter;
    });
  }

  private rawAnalysisHistoryRows(): AnalysisHistoryItem[] {
    return [...(this.analysisHistory || [])]
      .filter((row) => String(row.solvedAtIso || "").trim().length > 0)
      .sort((a, b) => new Date(b.solvedAtIso).getTime() - new Date(a.solvedAtIso).getTime());
  }

  historyAttemptCount(): number {
    return this.analysisHistoryRows().length;
  }

  historyAverageScore(): number {
    const rows = this.analysisHistoryRows();
    if (!rows.length) {
      return 0;
    }
    const total = rows.reduce((acc, row) => acc + (Number(row.score) || 0), 0);
    return Math.round((total / rows.length) * 10) / 10;
  }

  historyTrendLabel(): string {
    const rows = this.analysisHistoryRows();
    if (rows.length < 2) {
      return this.t("Not enough data", "Yeterli veri yok");
    }
    const latest = Number(rows[0].score) || 0;
    const older = Number(rows[Math.min(rows.length - 1, 4)].score) || 0;
    const diff = Math.round((latest - older) * 10) / 10;
    if (diff > 0.5) {
      return this.t(`Upward (+${diff})`, `Yukarı (+${diff})`);
    }
    if (diff < -0.5) {
      return this.t(`Downward (${diff})`, `Aşağı (${diff})`);
    }
    return this.t("Stable", "Stabil");
  }

  historyTopFocusTopics(limit = 5): string[] {
    const rows = this.analysisHistoryRows();
    const map = new Map<string, number>();
    for (const row of rows) {
      for (const topic of row.focusTopics || []) {
        const t = String(topic || "").trim();
        if (!t) {
          continue;
        }
        map.set(t, (map.get(t) || 0) + 1);
      }
    }
    return [...map.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, Math.max(1, limit))
      .map(([name]) => name);
  }

  resolvedScorePercent(): number {
    const sub = this.quizSubmission;
    if (sub) {
      return quizScorePercentFromAttempt({
        total_correct: sub.total_correct,
        total_wrong: sub.total_wrong,
        total_unanswered: sub.total_unanswered,
        total_question_count: sub.total_question_count,
        question_feedback: sub.question_feedback,
        score_percentage: sub.score_percentage,
      });
    }
    const picked = this.selectedHistoryRow();
    if (picked && picked.score != null) {
      return Math.max(0, Math.min(100, Math.round(Number(picked.score) || 0)));
    }
    if (this.lastQuizSummary && this.lastQuizSummary.score != null) {
      return Math.max(0, Math.min(100, Math.round(Number(this.lastQuizSummary.score) || 0)));
    }
    return 0;
  }

  private hashSeed(s: string): number {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
      h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
    }
    return Math.abs(h);
  }

  private mapErrorKeyToBucket(key: string): "concept" | "interpretation" | "careless" | "other" {
    const k = String(key || "")
      .trim()
      .toLowerCase();
    if (k === "unanswered") {
      return "other";
    }
    if (k === "interpretation") {
      return "interpretation";
    }
    if (k === "careless") {
      return "careless";
    }
    if (["concept_mixup", "definition", "recall", "formula_mixup"].includes(k)) {
      return "concept";
    }
    return "concept";
  }

  /** Kavram / yorumlama / dikkatsizlik — ham API etiketlerinden türetilmiş. */
  errorBucketRows(): { key: "concept" | "interpretation" | "careless"; count: number }[] {
    const acc = { concept: 0, interpretation: 0, careless: 0 };
    for (const e of this.errorEntries()) {
      if (e.key === "unanswered") {
        continue;
      }
      const b = this.mapErrorKeyToBucket(e.key);
      if (b === "other") {
        continue;
      }
      acc[b] += Math.max(0, Math.round(Number(e.count) || 0));
    }
    return [
      { key: "concept", count: acc.concept },
      { key: "interpretation", count: acc.interpretation },
      { key: "careless", count: acc.careless },
    ];
  }

  errorBucketTotal(): number {
    return this.errorBucketRows().reduce((a, r) => a + r.count, 0);
  }

  errorBucketPct(count: number): number {
    const t = this.errorBucketTotal();
    if (!t) {
      return 0;
    }
    return Math.round((Math.max(0, count) / t) * 100);
  }

  errorBucketLabel(key: "concept" | "interpretation" | "careless"): string {
    if (this.locale === "tr") {
      const m: Record<string, string> = {
        concept: "Kavram (Concept)",
        interpretation: "Yorumlama (Interpretation)",
        careless: "Dikkatsizlik (Careless)",
      };
      return m[key];
    }
    const m: Record<string, string> = {
      concept: "Concept",
      interpretation: "Interpretation",
      careless: "Careless",
    };
    return m[key];
  }

  dominantErrorBucketLine(): string {
    const rows = this.errorBucketRows();
    const t = this.errorBucketTotal();
    const tr = this.locale === "tr";
    if (!t) {
      return tr
        ? "Bu denemede sınıflı hata tipi kaydı yok; yeni quizlerde otomatik doldurulur."
        : "No classified mistake types for this attempt yet — newer quizzes fill this automatically.";
    }
    const top = [...rows].sort((a, b) => b.count - a.count || rows.indexOf(a) - rows.indexOf(b))[0];
    const lab = this.errorBucketLabel(top.key);
    const pct = this.errorBucketPct(top.count);
    return tr
      ? `En çok görülen hata tipi: ${lab} (${top.count} adet, yaklaşık %${pct}).`
      : `Most common mistake type: ${lab} (${top.count} misses, ~${pct}%).`;
  }

  unansweredErrorCount(): number {
    const s = this.quizSubmission?.error_type_summary;
    if (!s || s.unanswered == null) {
      return 0;
    }
    return Math.max(0, Math.round(Number(s.unanswered) || 0));
  }

  combinedRecommendationLines(): string[] {
    const dyn = this.dynamicRecommendationLines();
    const tips = (this.coachingPack?.personalized_tip_lines || []).map((x) => String(x).trim()).filter(Boolean);
    const seen = new Set<string>();
    const out: string[] = [];
    const push = (line: string) => {
      const t = line.trim();
      const k = t.toLowerCase();
      if (t.length < 6 || seen.has(k)) {
        return;
      }
      seen.add(k);
      out.push(t);
    };
    for (const x of dyn) {
      push(x);
    }
    for (const x of tips) {
      push(x);
    }
    return out.slice(0, 8);
  }

  /**
   * Skor, zayıf konular ve hata dağılımına göre deterministik ama kullanıcıya özel cümleler
   * (aynı metinleri herkese kopyalamaz).
   */
  dynamicRecommendationLines(): string[] {
    const tr = this.locale === "tr";
    const score = this.resolvedScorePercent();
    const weak = this.weakTopicRows()
      .map((r) => String(r.topic || "").trim())
      .filter(Boolean);
    const strong = this.strongTopicRows()
      .map((r) => String(r.topic || "").trim())
      .filter(Boolean);
    const buckets = this.errorBucketRows();
    const dom = [...buckets].sort((a, b) => b.count - a.count)[0];
    const seed = this.hashSeed(`${weak.join("|")}#${strong.join("|")}#${score}#${dom?.key || ""}`);
    const lines: string[] = [];

    const w0 = weak[0] || "";
    const w1 = weak[1] || "";
    const s0 = strong[0] || "";

    if (score < 55) {
      const v = seed % 2;
      lines.push(
        v === 0
          ? tr
            ? `Skorun %${score} — önce «${w0 || "en zayıf konu"}» için kısa özet + 3 soruluk tekrar en verimli adım.`
            : `Score ${score}% — a tight recap on “${w0 || "your weakest topic"}” plus three questions is the fastest next step.`
          : tr
            ? `Düşük skor normal; bugün tek hedef: «${w0 || "zayıf başlık"}» kavramını kendi cümlelerinle tanımla.`
            : `A low score is normal; today’s single goal: define “${w0 || "your weakest label"}” in your own words.`,
      );
    } else if (score < 80) {
      lines.push(
        tr
          ? `Skor %${score} — orta banttasın; «${w0 || "zayıf konu"}» ve «${w1 || "ikinci konu"}» üzerine karışık mini quiz skoru hızla yükseltir.`
          : `Score ${score}% — you are mid-band; a mixed micro-quiz on “${w0 || "weak topic A"}” and “${w1 || "weak topic B"}” usually lifts the score quickly.`,
      );
    } else {
      lines.push(
        tr
          ? `Skor %${score} — güçlüsün; «${s0 || "güçlü konu"}» dışında kalan ince boşlukları tek turda kapat.`
          : `Score ${score}% — you are strong; close the remaining thin gaps outside “${s0 || "your strongest topic"}” in one focused pass.`,
      );
    }

    if (w0 && w1) {
      const verbs = tr
        ? [`«${w1}» ile «${w0}» arasındaki farkı tek tabloya yaz.`, `Önce «${w0}», ardından «${w1}» için iki örnek çöz.`]
        : [`Write one table contrasting “${w1}” vs “${w0}”.`, `Drill “${w0}” first, then two worked examples on “${w1}”.`];
      lines.push(verbs[seed % verbs.length]);
    } else if (w0) {
      lines.push(
        tr
          ? `«${w0}» konusunu PDF asistanına “en sık karıştırılan yanlış anlama nedir?” diye sor.`
          : `Ask the assistant: “What is the most common misconception about ${w0}?”.`,
      );
    }

    if (dom && dom.count > 0) {
      if (dom.key === "interpretation") {
        lines.push(
          tr
            ? "Yorumlama hataları baskın — soru kökündeki kısıtlayıcı kelimeleri işaretle, cevabı metinde tek cümleyle bağla."
            : "Interpretation misses lead — underline qualifiers in the stem and tie each option to one exact sentence.",
        );
      } else if (dom.key === "careless") {
        lines.push(
          tr
            ? "Dikkatsizlik baskın — göndermeden önce 10 sn: şıkları soru köküyle tek tek eşleştir."
            : "Careless misses lead — take 10s before submit and match each option to an explicit stem clause.",
        );
      } else {
        lines.push(
          tr
            ? "Kavram hataları baskın — benzer terimleri yan yana yazıp tek cümlede ayrıştır."
            : "Concept misses lead — place similar terms side by side and state the difference in one sentence.",
        );
      }
    }

    const conf = this.quizSubmission?.confused_topics_ranked || [];
    const c0 = conf[0] && String(conf[0]).trim();
    if (c0) {
      lines.push(
        tr
          ? `«${c0}» tekrar listende öne çıkıyor — bu etikette 2 soruluk self-check yap.`
          : `“${c0}” keeps surfacing — run a 2-question self-check on that label.`,
      );
    }

    return lines.filter(Boolean).slice(0, 6);
  }

  miniQuizSummaryLine(): string {
    const p = this.buildMiniQuizPayload();
    const tr = this.locale === "tr";
    const diffLab =
      p.difficulty === "beginner"
        ? tr
          ? "kolay"
          : "easier"
        : p.difficulty === "technical"
        ? tr
          ? "zor"
          : "harder"
        : tr
        ? "karışık / orta"
        : "mixed / medium";
    const topics = (p.topics || []).join(", ") || (tr ? "(konu otomatik)" : "(topics auto)");
    return tr
      ? `Öneri: ${p.count} soru · ${diffLab} · odak: ${topics}`
      : `Suggested: ${p.count} questions · ${diffLab} · focus: ${topics}`;
  }

  buildMiniQuizPayload(): TailoredMiniQuizPayload {
    const score = this.resolvedScorePercent();
    let difficulty: "beginner" | "normal" | "technical" = "normal";
    if (score < 55) {
      difficulty = "beginner";
    } else if (score >= 80) {
      difficulty = "technical";
    }
    let n = score < 45 ? 3 : score >= 75 ? 5 : 4;
    n = Math.max(1, Math.min(15, n));

    const rq = this.coachingPack?.recommended_mini_quiz;
    if (rq?.num_questions != null) {
      const rn = Math.round(Number(rq.num_questions));
      if (Number.isFinite(rn) && rn >= 1) {
        n = Math.max(1, Math.min(15, rn));
      }
    }

    const weakT = this.weakTopicRows()
      .map((r) => String(r.topic || "").trim())
      .filter(Boolean);
    const missT = this.mostMissedTopics()
      .map((m) => String(m.topic || "").trim())
      .filter((t) => t && t !== "—");
    let topics = this.dedupeTopicNames([...weakT, ...missT]).slice(0, 6);
    if (!topics.length && rq?.focus_topics?.length) {
      topics = this.dedupeTopicNames((rq.focus_topics || []).map((x) => String(x).trim())).slice(0, 6);
    }
    if (!topics.length) {
      const w = this.weakestTopicName().trim();
      if (w) {
        topics = [w];
      }
    }

    const strongLabels = this.strongTopicRows()
      .map((r) => String(r.topic || "").trim())
      .filter(Boolean);
    const salt = `${topics.join("|")}::${n}::${score}::${Date.now()}`;
    const challengeTopics = this.pickChallengeTopicsForMini(strongLabels, salt);

    return {
      count: n,
      topics,
      difficulty,
      challengeTopics: challengeTopics.length ? challengeTopics : undefined,
    };
  }

  private dedupeTopicNames(items: string[]): string[] {
    const out: string[] = [];
    const seen = new Set<string>();
    for (const raw of items) {
      const t = String(raw || "").trim();
      if (!t) {
        continue;
      }
      const k = t.toLowerCase();
      if (seen.has(k)) {
        continue;
      }
      seen.add(k);
      out.push(t);
    }
    return out;
  }

  private pickChallengeTopicsForMini(candidates: string[], salt: string): string[] {
    if (!candidates.length) {
      return [];
    }
    const scored = candidates.map((t) => ({ t, s: this.hashSeed(t + "::" + salt) }));
    scored.sort((a, b) => a.s - b.s);
    const out: string[] = [];
    const seen = new Set<string>();
    for (const { t } of scored) {
      const k = t.toLowerCase();
      if (seen.has(k)) {
        continue;
      }
      seen.add(k);
      out.push(t);
      if (out.length >= 2) {
        break;
      }
    }
    return out;
  }

  /** Şablon + kaynak API için odak konusu (zayıf / seçili konu). */
  resourceTopic(): string {
    const selected = String(this.selectedFocusTopic || "").trim();
    if (selected) {
      return selected;
    }
    const weak = this.weakestTopicName().trim();
    if (weak) {
      return weak;
    }
    const missed = this.mostMissedTopics()[0]?.topic?.trim();
    if (missed && missed !== "—") {
      return missed;
    }
    return this.locale === "tr" ? "ders çalışma teknikleri" : "study techniques";
  }

  openVideoResource(): void {
    const topic = this.resourceTopic();
    const q = this.locale === "tr" ? `${topic} konu anlatımı` : `${topic} lecture`;
    const url = `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`;
    window.open(url, "_blank", "noopener");
  }

  openWebResource(): void {
    const topic = this.resourceTopic();
    const q = this.locale === "tr" ? `${topic} konu özeti kaynak` : `${topic} study resources`;
    const url = `https://www.google.com/search?q=${encodeURIComponent(q)}`;
    window.open(url, "_blank", "noopener");
  }

  resourceTipsList(): StudyResourceTip[] {
    const raw = this.coachingPack?.resource_tips || [];
    return raw.filter((t) => {
      if (!t) {
        return false;
      }
      const blob = `${t.topic || ""}${t.short_summary || ""}${t.explain_action || ""}${t.pdf_section_hint || ""}${t.mini_quiz_hint || ""}`.trim();
      return blob.length > 0;
    });
  }

  recommendedMiniQuizCaption(): string {
    return String(this.coachingPack?.recommended_mini_quiz?.caption || "").trim();
  }

  loadCuratedResources(): void {
    if (!this.hasShell) {
      this.curatedResources = null;
      this.curatedLoading = false;
      this.curatedError = null;
      this.curatedFetchKey = "";
      this.curatedFetchInFlight = false;
      return;
    }
    const loc = this.locale === "tr" ? "tr" : "en";
    const topic = this.resourceTopic();
    const fetchKey = `${loc}|${topic}`;
    if (this.curatedFetchInFlight && this.curatedFetchKey === fetchKey) {
      return;
    }
    this.curatedFetchKey = fetchKey;
    this.curatedFetchInFlight = true;
    this.curatedLoading = true;
    this.curatedError = null;
    this.api
      .getBestResources(topic, loc)
      .pipe(take(1))
      .subscribe({
        next: (r) => {
          if (this.curatedFetchKey !== fetchKey) {
            return;
          }
          this.curatedResources = r;
          this.curatedLoading = false;
          this.curatedFetchInFlight = false;
        },
        error: (err: unknown) => {
          if (this.curatedFetchKey !== fetchKey) {
            return;
          }
          this.curatedError = readApiError(err, this.t("Could not load links.", "Bağlantılar yüklenemedi."));
          this.curatedLoading = false;
          this.curatedFetchInFlight = false;
        },
      });
  }

  openCuratedVideo(): void {
    const u = this.curatedResources?.video_url?.trim();
    if (u) {
      window.open(u, "_blank", "noopener");
    }
  }

  openCuratedWeb(): void {
    const u = this.curatedResources?.web_url?.trim();
    if (u) {
      window.open(u, "_blank", "noopener");
    }
  }
}
