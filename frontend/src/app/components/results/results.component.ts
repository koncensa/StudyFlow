// cmp: results | tr: quiz sonuçları ekranı — skor, konu analizi, öneriler / en: quiz results screen score topic analysis suggestions

import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from "@angular/core";

import { environment } from "../../../environments/environment";
import { AppLang } from "../../i18n/app-lang";
import {
  AdaptiveStudyPlan,
  LastQuizSummary,
  QuizCoachingPack,
  QuizLearningBrief,
  QuizQuestionFeedback,
  QuizSubmissionResponse,
  SuggestionResponse,
  TailoredMiniQuizPayload,
  TopicAnalysisResponse,
  TopicPerformance,
  TopicProgressItem,
  TopicScoreRow,
  TopicStudyContent,
} from "../../models/types";
import { ChartOptions } from "chart.js";
import {
  extractWrongAnswers,
  groupWrongByTopic,
  WrongAnswerRow,
} from "../../utils/quiz-results-pipeline";
import { quizScorePercentFromAttempt } from "../../utils/quiz-score-display";
import { feedbackRowIsCorrect, feedbackRowIsUnanswered } from "../../utils/quiz-submission-normalize";

@Component({
  selector: "app-results",
  templateUrl: "./results.component.html",
  styleUrls: ["./results.component.css"],
})
export class ResultsComponent implements OnChanges {
  showInsightExpanded = false;
  showPlanExpanded = false;
  showBreakdownExpanded = false;
  /** Which wrong answer (index into `allWrongRows()`). */
  mistakeCarouselIndex = 0;
  /**
   * Which screen within that wrong: 0 = soru metni, 1–4 = dört koçluk bölümü.
   * Sadece kullanıcı «Geri / İleri» ile değişir; otomatik geçiş yok.
   */
  mistakeReadPhase = 0;
  readonly mistakeReadPhaseLast = 4;
  /** Topic breakdown for the latest quiz attempt (primary). */
  @Input() topicAnalysis: TopicAnalysisResponse | null = null;
  /** Aggregated across all quizzes for this user (secondary). */
  @Input() lifetimeTopicAnalysis: TopicAnalysisResponse | null = null;
  @Input() topicProgress: TopicProgressItem[] = [];
  @Input() quizSubmission: QuizSubmissionResponse | null = null;
  @Input() suggestions: SuggestionResponse | null = null;
  @Input() lastQuizSummary: LastQuizSummary | null = null;
  @Input() resultsLoading = false;
  /** Parent sets when GET /quiz/user-results fails. */
  @Input() resultsError: string | null = null;
  @Input() locale: AppLang = "en";
  @Input() hasActivePdf = false;

  @Output() startPomodoro = new EventEmitter<string | null>();
  @Output() requestWeakTopicQuiz = new EventEmitter<void>();
  @Output() startMiniWeakQuiz = new EventEmitter<number>();
  @Output() startCoachingQuiz = new EventEmitter<void>();
  /** Banded weak-topic mini quiz from coaching_pack.recommended_mini_quiz */
  @Output() startTailoredMiniQuiz = new EventEmitter<TailoredMiniQuizPayload>();
  @Output() startMistakesQuiz = new EventEmitter<void>();
  @Output() startEasyRetry = new EventEmitter<void>();
  @Output() openAssistant = new EventEmitter<void>();
  @Output() retryLoadResults = new EventEmitter<void>();
  /** Navigate parent to Analysis tab (deep coaching lives there). */
  @Output() openAnalysisTab = new EventEmitter<void>();

  /** Avoid `suggestions?.x` in templates (older Angular template parser / Ivy edge cases). */
  get adaptivePlan(): AdaptiveStudyPlan | null {
    const s = this.suggestions;
    return s && s.adaptive_plan ? s.adaptive_plan : null;
  }

  get coachingPack(): QuizCoachingPack | null {
    const s = this.suggestions;
    return s && s.coaching_pack ? s.coaching_pack : null;
  }

  /** Server-built weak / mid / strong snapshot for this attempt (personalized). */
  get learningBrief(): QuizLearningBrief | null {
    return this.quizSubmission?.learning_brief ?? null;
  }

  get coachMessage(): string | null {
    const s = this.suggestions;
    if (!s || s.coach_message == null) {
      return null;
    }
    const txt = String(s.coach_message).trim();
    return txt.length ? txt : null;
  }

  topicChartType = "bar";
  topicChartOptions: ChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      yAxes: [{ ticks: { min: 0, max: 100, callback: (v: any) => `${v}%` } }],
    },
  };
  topicChartData: any = {
    labels: [],
    datasets: [{ data: [], backgroundColor: "#2563eb" }],
  };

  distributionChartType = "doughnut";
  distributionChartOptions: ChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { position: "bottom" } },
  };
  distributionChartData: any = {
    labels: [],
    datasets: [{ data: [], backgroundColor: [] }],
  };

  statusClass(status: string): string {
    const s = status === "moderate" ? "developing" : status;
    if (s === "strong") return "text-success";
    if (s === "good") return "text-primary";
    if (s === "developing") return "text-warning";
    if (s === "weak") return "text-danger";
    if (s === "very_weak") return "text-danger";
    return "text-secondary";
  }

  statusLabel(status: string): string {
    const s = status === "moderate" ? "developing" : status;
    const tr = this.locale === "tr";
    const map: Record<string, string> = tr
      ? {
          very_weak: "Çok zayıf (0–40%)",
          weak: "Zayıf (40–60%)",
          developing: "Geliştir (60–75%)",
          good: "İyi (75–90%)",
          strong: "Güçlü (90%+)",
          moderate: "Geliştir (60–75%)",
        }
      : {
          very_weak: "Very weak (0–40%)",
          weak: "Weak (40–60%)",
          developing: "Developing (60–75%)",
          good: "Good (75–90%)",
          strong: "Strong (90%+)",
          moderate: "Developing (60–75%)",
        };
    return map[s] ?? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");
  }

  onPomodoro(topic: string) {
    this.startPomodoro.emit(topic);
  }

  t(msg: string): string {
    return msg;
  }

  toggleInsightExpanded(): void {
    this.showInsightExpanded = !this.showInsightExpanded;
  }

  togglePlanExpanded(): void {
    this.showPlanExpanded = !this.showPlanExpanded;
  }

  toggleBreakdownExpanded(): void {
    this.showBreakdownExpanded = !this.showBreakdownExpanded;
  }

  /** Same rule as quiz scoring: correct / total (submission first, else summary). */
  private resolvedAttemptPercent(): number {
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
    const l = this.lastQuizSummary;
    if (l && l.totalQuestions > 0) {
      return quizScorePercentFromAttempt({
        total_correct: l.correct,
        total_wrong: l.wrong,
        total_unanswered: l.unanswered,
        total_question_count: l.totalQuestions,
        score_percentage: l.score,
      });
    }
    return 0;
  }

  coachEnergyClass(): string {
    const score = this.resolvedAttemptPercent();
    if (score >= 85) {
      return "sf-coachHero__mood--great";
    }
    if (score >= 55) {
      return "sf-coachHero__mood--steady";
    }
    return "sf-coachHero__mood--urgent";
  }

  coachHeadline(): string {
    const score = this.resolvedAttemptPercent();
    const tr = this.locale === "tr";
    if (score >= 85) {
      return tr ? "Harika tempo" : "Great momentum";
    }
    if (score >= 70) {
      return tr ? "Sağlam gidiyorsun" : "Solid momentum";
    }
    if (score >= 55) {
      return tr ? "Yoldasın, şimdi keskinleştir" : "On track, now sharpen";
    }
    if (score >= 40) {
      return tr ? "Temel oturuyor; netleştir" : "Foundations forming — tighten up";
    }
    return tr ? "Odaklan, toparlayalım" : "Refocus, let's rebuild";
  }

  coachSubline(): string {
    const score = this.resolvedAttemptPercent();
    const tr = this.locale === "tr";
    if (score >= 85) {
      return tr ? "Güçlü gidiyorsun. Zayıf noktalara kısa bir dokunuş yeter." : "You are strong. A short pass on weak spots is enough.";
    }
    if (score >= 70) {
      return tr ? "İyi taban; kalan boşlukları hedefli mini quiz ile kapat." : "Good base — targeted micro-quizzes close the remaining gaps.";
    }
    if (score >= 55) {
      return tr ? "Temel iyi. En zayıf konuyu kapatırsan skor hızla artar." : "Base is good. Fix your weakest topic and score rises fast.";
    }
    if (score >= 40) {
      return tr
        ? "Bir üst bant için önce en çok hata yaptığın konuyu tek başlıkta toparla."
        : "To reach the next band, rebuild your noisiest topic in one focused pass.";
    }
    return tr ? "Skor düşük; zayıf başlıkları sırayla toparlamak en hızlı yol." : "Score is low — rebuilding weak labels in order is the fastest lift.";
  }

  smartProgressPercent(): number {
    const score = this.resolvedAttemptPercent();
    return Math.max(0, Math.min(100, Math.round(score)));
  }

  smartWeakTopicLabel(): string {
    const weak = this.priorityWeakRows()[0]?.topic;
    const tr = this.locale === "tr";
    if (weak) {
      return tr ? `Zayıf konu: ${weak}` : `Weak topic: ${weak}`;
    }
    return tr ? "Zayıf konu: belirgin değil" : "Weak topic: not obvious yet";
  }

  smartImproveLine(): string {
    const tr = this.locale === "tr";
    const p = this.topicProgress?.find((x) => x.trend === "improved");
    if (p && p.previous_success_rate != null) {
      const prev = Math.round(p.previous_success_rate * 100);
      const cur = Math.round(p.current_success_rate * 100);
      return tr
        ? `${p.topic} konusunda %${prev} → %${cur} gelişim var.`
        : `You improved on ${p.topic} from ${prev}% to ${cur}%.`;
    }
    return tr
      ? "Odaklı tekrar ile skoru hızlıca yükseltebilirsin."
      : "Focused practice can move your score up quickly.";
  }

  smartNextStepLine(): string {
    const tr = this.locale === "tr";
    const n = Math.max(1, Math.min(15, Math.round(Number(this.quizSubmission?.recommended_mini_quiz_count ?? 3) || 3)));
    return tr ? `Sonraki adım: ${n} soruluk mini quiz.` : `Next step: Take a ${n}-question mini quiz.`;
  }

  mistakeWhyShort(row: QuizQuestionFeedback): string {
    const text = String(row.why_wrong || "").trim();
    if (!text) {
      return this.locale === "tr" ? "Yanlışın ana nedeni: kavram/yorum uyumsuzluğu." : "Main issue: concept/prompt mismatch.";
    }
    const oneLine = text.replace(/\s+/g, " ");
    if (oneLine.length <= 150) {
      return oneLine;
    }
    return `${oneLine.slice(0, 147)}...`;
  }

  snapshotWrong(): string {
    const block = this.whyWrongInsightBlock();
    if (block.show && block.headline.trim().length) {
      return block.headline;
    }
    const tr = this.locale === "tr";
    const wrong = Number(this.lastQuizSummary?.wrong ?? 0);
    return tr ? `${wrong} soruda ana fikir veya soru kökü kaçtı.` : `${wrong} answers missed key concept or prompt wording.`;
  }

  snapshotFix(): string {
    const weak = this.priorityWeakRows()[0];
    const tr = this.locale === "tr";
    if (weak) {
      return tr
        ? `Önce "${weak.topic}" (${weak.pct}%) konusunu düzelt.`
        : `Fix "${weak.topic}" first (${weak.pct}%).`;
    }
    const top = this.personalizedRecommendationLines()[0];
    if (top) {
      return top;
    }
    return tr ? "En çok yanlış yaptığın soru tipini tekrar et." : "Review the question type you missed most.";
  }

  snapshotNext(): string {
    const tr = this.locale === "tr";
    const plan = this.dailyStudyPlanItems()[0];
    if (plan?.title) {
      return tr ? `Şimdi: ${plan.title}` : `Now: ${plan.title}`;
    }
    return tr ? "Şimdi 3 soruluk mini quiz başlat." : "Now start a 3-question mini quiz.";
  }

  priorityWeakRows(): { topic: string; pct: number }[] {
    const weak = this.weaknessMapBands().weak;
    if (weak.length) {
      return weak.slice(0, 6);
    }
    return this.weaknessMapBands().medium.slice(0, 4);
  }

  /** e.g. "1/2 · " before the percent when coaching rows include counts */
  topicAttemptLabel(row: TopicScoreRow): string {
    const t = row.total_attempts;
    const w = row.wrong_count;
    if (t == null || w == null) {
      return "";
    }
    const tt = Math.max(0, Math.round(Number(t)));
    const ww = Math.max(0, Math.round(Number(w)));
    const c = Math.max(0, tt - ww);
    return `${c}/${tt} · `;
  }

  topicsWithMistakes(): TopicPerformance[] {
    const ta = this.topicAnalysis;
    if (!ta?.topics?.length) {
      return [];
    }
    return [...ta.topics].filter((t) => (t.wrong_count || 0) > 0).sort((a, b) => (b.wrong_count || 0) - (a.wrong_count || 0));
  }

  onWeakQuizClick(): void {
    this.requestWeakTopicQuiz.emit();
  }

  /** Mini quiz count + topic focus (same PDF session) — uses coaching pack when present. */
  emitSuggestedMiniQuizCount(): void {
    const raw = this.quizSubmission?.recommended_mini_quiz_count;
    const n = raw != null ? Number(raw) : NaN;
    const c = Number.isFinite(n) && n >= 1 ? Math.min(15, Math.round(n)) : 3;
    const p = this.coachingPack;
    const fromRec = p?.recommended_mini_quiz?.focus_topics;
    if (fromRec && fromRec.length) {
      const topics = fromRec.map((x) => String(x).trim()).filter(Boolean);
      this.startTailoredMiniQuiz.emit({ count: c, topics });
      return;
    }
    const topics = this.inferredMiniQuizFocusTopics();
    this.startTailoredMiniQuiz.emit({ count: c, topics });
  }

  private inferredMiniQuizFocusTopics(): string[] {
    const sub = this.quizSubmission;
    if (!sub) {
      return [];
    }
    const suggested = (sub.suggested_mini_quiz_topic || "").trim();
    if (suggested) {
      return [suggested];
    }
    const ranked = [...(sub.confused_topics_ranked || [])].map((x) => String(x).trim()).filter(Boolean);
    if (ranked.length) {
      return ranked.slice(0, 8);
    }
    const weak = [...(sub.weak_topics || [])].map((x) => String(x).trim()).filter(Boolean);
    if (weak.length) {
      return weak.slice(0, 8);
    }
    const tp = sub.topic_analysis?.topics || [];
    const low = [...tp]
      .filter((t) => (t.success_rate || 0) < 0.65 && (t.topic || "").trim())
      .sort((a, b) => (a.success_rate || 0) - (b.success_rate || 0))
      .map((t) => String(t.topic).trim());
    return low.slice(0, 8);
  }

  coachingBandLabel(band: string | undefined): string {
    const b = (band || "").toLowerCase();
    const tr = this.locale === "tr";
    if (b === "critical") {
      return tr ? "Kritik (0–40%)" : "Critical (0–40%)";
    }
    if (b === "building") {
      return tr ? "Geliştirme (40–70%)" : "Building (40–70%)";
    }
    if (b === "strong") {
      return tr ? "Güçlü (70%+)" : "Strong (70%+)";
    }
    return band || "";
  }

  coachingQuizCount(pack: QuizCoachingPack): number {
    const raw = pack && pack.suggested_quiz ? pack.suggested_quiz.num_questions : null;
    const n = raw != null ? Number(raw) : NaN;
    if (Number.isNaN(n)) {
      return 5;
    }
    return Math.max(1, Math.min(15, Math.round(n)));
  }

  coachingDifficulty(pack: QuizCoachingPack): string {
    const d = pack && pack.suggested_quiz && pack.suggested_quiz.difficulty ? String(pack.suggested_quiz.difficulty) : "";
    return d.trim() || "normal";
  }

  tailoredMiniCount(pack: QuizCoachingPack): number {
    const rq = pack?.recommended_mini_quiz;
    const fromRec = rq?.num_questions != null ? Number(rq.num_questions) : NaN;
    if (!Number.isNaN(fromRec) && fromRec > 0) {
      return Math.max(1, Math.min(15, Math.round(fromRec)));
    }
    return this.coachingQuizCount(pack);
  }

  emitTailoredMiniQuiz(): void {
    const p = this.coachingPack;
    const rq = p && p.recommended_mini_quiz ? p.recommended_mini_quiz : null;
    if (rq) {
      const n = this.tailoredMiniCount(p);
      const topics = (rq.focus_topics || []).map((x) => String(x).trim()).filter(Boolean);
      this.startTailoredMiniQuiz.emit({ count: n, topics });
      return;
    }
    this.emitSuggestedMiniQuizCount();
  }

  topicSuccessPct(block: TopicStudyContent): number {
    const raw = block && block.success_rate_pct != null ? Number(block.success_rate_pct) : NaN;
    return Number.isNaN(raw) ? 0 : Math.round(raw);
  }

  wrongFeedbackList(): QuizQuestionFeedback[] {
    const fb = this.quizSubmission?.question_feedback;
    if (!fb?.length) {
      return [];
    }
    return fb.filter((x) => !feedbackRowIsCorrect(x) && !feedbackRowIsUnanswered(x));
  }

  unansweredFeedbackList(): QuizQuestionFeedback[] {
    const fb = this.quizSubmission?.question_feedback;
    if (!fb?.length) {
      return [];
    }
    return fb.filter((x) => feedbackRowIsUnanswered(x));
  }

  questionReviewRows(): QuizQuestionFeedback[] {
    const fb = this.quizSubmission?.question_feedback;
    if (!fb?.length) {
      return [];
    }
    return [...fb].sort((a, b) => a.question_index - b.question_index);
  }

  reviewStatusLabel(f: QuizQuestionFeedback): string {
    if (feedbackRowIsUnanswered(f)) {
      return this.locale === "tr" ? "Boş" : "Blank";
    }
    return feedbackRowIsCorrect(f)
      ? this.locale === "tr"
        ? "Doğru"
        : "Correct"
      : this.locale === "tr"
      ? "Yanlış"
      : "Wrong";
  }

  reviewStatusClass(f: QuizQuestionFeedback): string {
    if (feedbackRowIsUnanswered(f)) {
      return "text-secondary";
    }
    return feedbackRowIsCorrect(f) ? "text-success" : "text-danger";
  }

  overviewScore(): number {
    return Math.max(0, Math.min(100, Math.round(this.resolvedAttemptPercent())));
  }

  overviewCorrect(): number {
    if (this.lastQuizSummary) {
      return Math.max(0, Math.round(Number(this.lastQuizSummary.correct) || 0));
    }
    return Math.max(0, Math.round(Number(this.quizSubmission?.total_correct ?? 0)));
  }

  overviewWrong(): number {
    if (this.lastQuizSummary) {
      return Math.max(0, Math.round(Number(this.lastQuizSummary.wrong) || 0));
    }
    return Math.max(0, Math.round(Number(this.quizSubmission?.total_wrong ?? 0)));
  }

  /** Boş bırakılan sorular — özet ve analizle aynı kaynak (submission / son özet / geri bildirim). */
  overviewBlank(): number {
    if (this.lastQuizSummary) {
      return Math.max(0, Math.round(Number(this.lastQuizSummary.unanswered) || 0));
    }
    const sub = this.quizSubmission;
    if (sub && sub.total_unanswered != null && Number.isFinite(Number(sub.total_unanswered))) {
      return Math.max(0, Math.round(Number(sub.total_unanswered)));
    }
    return this.reviewBlankCount();
  }

  overviewTotalQuestions(): number {
    if (this.lastQuizSummary) {
      return Math.max(0, Math.round(Number(this.lastQuizSummary.totalQuestions) || 0));
    }
    const fromSubmission = Math.max(0, Math.round(Number(this.quizSubmission?.total_question_count ?? 0)));
    if (fromSubmission > 0) {
      return fromSubmission;
    }
    return this.questionReviewRows().length;
  }

  overviewComment(): string {
    const tr = this.locale === "tr";
    const score = this.resolvedAttemptPercent();
    if (score >= 85) {
      return tr
        ? "Çok iyi — soruların çoğunu doğru bağladın; güçlü konuları aralıklı tekrarla koru."
        : "Excellent — you connected most items correctly; keep strong areas fresh with spaced review.";
    }
    if (score >= 70) {
      return tr
        ? "Sağlam sonuç — birkaç konuda pratikle üst banta çıkmak mümkün."
        : "Solid result — a bit more practice on a few topics can push you into the top band.";
    }
    if (score >= 55) {
      return tr
        ? "Dengeli — zayıf kalan başlıklara odaklanınca skor hızla yükselir."
        : "Balanced — focusing the weaker headings moves the score up quickly.";
    }
    if (score >= 40) {
      return tr
        ? "Gelişim alanı — temel kavramları netleştirip aynı tür soruları tekrar et."
        : "Growth zone — clarify core ideas, then repeat the same question styles.";
    }
    return tr
      ? "Temel güçlendirme — önce en zayıf konuyu kısa özet + birkaç soru ile toparla."
      : "Foundation work — rebuild your weakest topic with a short recap plus a few questions.";
  }

  /** Callout styles for the quick-comment block (score band). */
  overviewCommentInsightClass(): string {
    const s = this.resolvedAttemptPercent();
    if (s >= 85) {
      return "sf-insight sf-insight--excellent";
    }
    if (s >= 70) {
      return "sf-insight sf-insight--solid";
    }
    if (s >= 55) {
      return "sf-insight sf-insight--balanced";
    }
    if (s >= 40) {
      return "sf-insight sf-insight--growth";
    }
    return "sf-insight sf-insight--foundation";
  }

  overviewCommentBandTag(): string {
    const tr = this.locale === "tr";
    const s = this.resolvedAttemptPercent();
    if (s >= 85) {
      return tr ? "85%+" : "85%+";
    }
    if (s >= 70) {
      return tr ? "70–84%" : "70–84%";
    }
    if (s >= 55) {
      return tr ? "55–69%" : "55–69%";
    }
    if (s >= 40) {
      return tr ? "40–54%" : "40–54%";
    }
    return tr ? "0–39%" : "0–39%";
  }

  reviewCardClass(f: QuizQuestionFeedback): string {
    if (feedbackRowIsUnanswered(f)) {
      return "sf-reviewCard sf-reviewCard--blank";
    }
    return feedbackRowIsCorrect(f) ? "sf-reviewCard sf-reviewCard--correct" : "sf-reviewCard sf-reviewCard--wrong";
  }

  reviewStatusPillClass(f: QuizQuestionFeedback): string {
    if (feedbackRowIsUnanswered(f)) {
      return "sf-reviewPill sf-reviewPill--blank";
    }
    return feedbackRowIsCorrect(f) ? "sf-reviewPill sf-reviewPill--correct" : "sf-reviewPill sf-reviewPill--wrong";
  }

  reviewUserAnswerText(f: QuizQuestionFeedback): string {
    const picked = String(f.selected_answer || "").trim();
    if (picked) {
      return picked;
    }
    return this.locale === "tr" ? "(bos)" : "(blank)";
  }

  reviewPreviewText(f: QuizQuestionFeedback): string {
    const text = String(f.question_text || "").replace(/\s+/g, " ").trim();
    if (!text) {
      return this.locale === "tr" ? "Soru metni bulunamadi." : "Question text is not available.";
    }
    const maxLen = 120;
    if (text.length <= maxLen) {
      return text;
    }
    const chunk = text.slice(0, maxLen);
    const sentenceCut = Math.max(chunk.lastIndexOf(". "), chunk.lastIndexOf("? "), chunk.lastIndexOf("! "));
    if (sentenceCut >= Math.floor(maxLen * 0.55)) {
      return `${chunk.slice(0, sentenceCut + 1).trim()}...`;
    }
    const wordCut = chunk.lastIndexOf(" ");
    if (wordCut >= Math.floor(maxLen * 0.55)) {
      return `${chunk.slice(0, wordCut).trim()}...`;
    }
    return `${chunk.trim()}...`;
  }

  reviewCorrectCount(): number {
    return this.questionReviewRows().filter((x) => feedbackRowIsCorrect(x)).length;
  }

  reviewWrongCount(): number {
    return this.questionReviewRows().filter((x) => !feedbackRowIsCorrect(x) && !feedbackRowIsUnanswered(x)).length;
  }

  reviewBlankCount(): number {
    return this.questionReviewRows().filter((x) => feedbackRowIsUnanswered(x)).length;
  }

  rowIsUnanswered(f: QuizQuestionFeedback): boolean {
    return feedbackRowIsUnanswered(f);
  }

  allWrongRows(): WrongAnswerRow[] {
    return extractWrongAnswers(this.quizSubmission ?? undefined);
  }

  /** Wrong-answer rows in stable order for the step-by-step coach. */
  mistakeWizardRows(): WrongAnswerRow[] {
    return this.allWrongRows();
  }

  activeMistakeRow(): WrongAnswerRow | null {
    const rows = this.mistakeWizardRows();
    if (!rows.length) {
      return null;
    }
    const i = Math.max(0, Math.min(this.mistakeCarouselIndex, rows.length - 1));
    return rows[i];
  }

  mistakeWizardTotal(): number {
    return this.mistakeWizardRows().length;
  }

  /** Soru + iç ekran konumu (otomatik ilerleme yok). */
  mistakeReadStepLabel(): string {
    const n = this.mistakeWizardTotal();
    const q = Math.min(this.mistakeCarouselIndex + 1, Math.max(1, n));
    const p = Math.min(this.mistakeReadPhase + 1, this.mistakeReadPhaseLast + 1);
    return this.locale === "tr"
      ? `Soru ${q}/${n} · Ekran ${p}/${this.mistakeReadPhaseLast + 1}`
      : `Question ${q}/${n} · Screen ${p}/${this.mistakeReadPhaseLast + 1}`;
  }

  mistakeReadPhaseTitle(): string {
    const tr = this.locale === "tr";
    switch (this.mistakeReadPhase) {
      case 0:
        return tr ? "Soru metni" : "Question stem";
      case 1:
        return tr ? "Senin hatan" : "Your mistake";
      case 2:
        return tr ? "Neden yanlış" : "Why it does not fit";
      case 3:
        return tr ? "Doğru düşünce" : "Correct thinking";
      default:
        return tr ? "Doğru cevap ve sonraki adım" : "Correct answer & follow-up";
    }
  }

  /** 0–100: tüm sorular × ekranlar boyunca (yalnızca görsel; otomatik adım yok). */
  mistakeProgressPercent(): number {
    const n = this.mistakeWizardTotal();
    if (!n) {
      return 0;
    }
    const steps = n * (this.mistakeReadPhaseLast + 1);
    const done = this.mistakeCarouselIndex * (this.mistakeReadPhaseLast + 1) + this.mistakeReadPhase + 1;
    return Math.round((done / steps) * 1000) / 10;
  }

  mistakeStudioKicker(): string {
    return this.locale === "tr" ? "Yanlışlardan öğren" : "Guided review";
  }

  mistakeWizardIntro(): string {
    return this.locale === "tr"
      ? "Her ekranda tek bir parça var: önce soru metni, sonra dört koçluk adımı. Otomatik geçiş yok — okuyup «İleri» veya numaraya tıklayarak kendin ilerle."
      : "Each screen shows one piece: stem first, then four coaching steps. Nothing auto-advances — read, then tap Next or a question number.";
  }

  mistakeMetaSummaryLabel(): string {
    return this.locale === "tr" ? "Bu denemenin özeti (konular, rozetler)" : "Attempt summary (topics, badges)";
  }

  setMistakeStep(i: number): void {
    const rows = this.mistakeWizardRows();
    if (!rows.length) {
      return;
    }
    this.mistakeCarouselIndex = Math.max(0, Math.min(Math.floor(i), rows.length - 1));
    this.mistakeReadPhase = 0;
  }

  canMistakeReadAdvance(): boolean {
    const n = this.mistakeWizardTotal();
    return !(this.mistakeCarouselIndex >= n - 1 && this.mistakeReadPhase >= this.mistakeReadPhaseLast);
  }

  canMistakeReadBack(): boolean {
    return !(this.mistakeCarouselIndex <= 0 && this.mistakeReadPhase <= 0);
  }

  bumpMistakeReadPhase(delta: number): void {
    const n = this.mistakeWizardTotal();
    if (!n) {
      return;
    }
    const last = this.mistakeReadPhaseLast;
    if (delta > 0) {
      if (this.mistakeReadPhase < last) {
        this.mistakeReadPhase += 1;
      } else if (this.mistakeCarouselIndex < n - 1) {
        this.mistakeCarouselIndex += 1;
        this.mistakeReadPhase = 0;
      }
      return;
    }
    if (delta < 0) {
      if (this.mistakeReadPhase > 0) {
        this.mistakeReadPhase -= 1;
      } else if (this.mistakeCarouselIndex > 0) {
        this.mistakeCarouselIndex -= 1;
        this.mistakeReadPhase = last;
      }
    }
  }

  wrongRowsByTopic(): { topic: string; rows: WrongAnswerRow[] }[] {
    const m = groupWrongByTopic(this.allWrongRows());
    return [...m.entries()]
      .map(([topic, rows]) => ({ topic, rows }))
      .sort((a, b) => b.rows.length - a.rows.length);
  }

  errorSummaryEntries(): { key: string; count: number }[] {
    const s = this.quizSubmission?.error_type_summary;
    if (!s) {
      return [];
    }
    return Object.keys(s).map((k) => ({ key: k, count: s[k] }));
  }

  /** Mistake card: four mandatory sections (fallbacks for pre-schema attempts). */
  mistakeYourMistakeText(row: WrongAnswerRow): string {
    const s = row.yourMistake?.trim();
    if (s) {
      return s;
    }
    const tr = this.locale === "tr";
    const pick = (row.userAnswer || "").trim() || (tr ? "(seçim yok)" : "(no selection)");
    const et = row.errorType ? this.errorTypeLabel(row.errorType) : tr ? "Genel" : "General";
    return tr ? `«${pick}» seçtin. Hata türü: ${et}.` : `You selected «${pick}». Error type: ${et}.`;
  }

  mistakeWhyIncorrectText(row: WrongAnswerRow): string {
    return row.whyIncorrect?.trim() || row.whyWrong?.trim() || (this.locale === "tr" ? "—" : "—");
  }

  mistakeCorrectThinkingText(row: WrongAnswerRow): string {
    return row.correctThinking?.trim() || row.teachingSnippet?.trim() || (this.locale === "tr" ? "—" : "—");
  }

  mistakeCorrectExplainedText(row: WrongAnswerRow): string {
    const e = row.correctAnswerExplained?.trim();
    if (e) {
      return e;
    }
    const tr = this.locale === "tr";
    const c = (row.correctAnswer || "").trim();
    if (c) {
      return tr
        ? `Doğru şık: «${c}». (Bu kayıtta ayrıntılı paragraf yok; yeni quizlerde otomatik üretilir.)`
        : `Correct option: «${c}». (This saved attempt has no long rationale; take a new quiz for the full breakdown.)`;
    }
    return "—";
  }

  mistakeSectionHeading(kind: "your" | "why" | "think" | "answer"): string {
    const tr = this.locale === "tr";
    if (kind === "your") {
      return tr ? "Senin hatan" : "Your mistake";
    }
    if (kind === "why") {
      return tr ? "Neden yanlış" : "Why it does not fit";
    }
    if (kind === "think") {
      return tr ? "Doğru düşünce" : "Correct thinking";
    }
    return tr ? "Doğru cevap" : "Correct answer";
  }

  errorTypeLabel(code: string): string {
    if (this.locale === "tr") {
      const map: Record<string, string> = {
        concept_mixup: "Kavram karışması",
        formula_mixup: "Formül / hesap",
        careless: "Dikkat / okuma",
        interpretation: "Yorumlama",
        unanswered: "Boş",
        definition: "Tanım",
        recall: "Hatırlama",
      };
      return map[code] ?? code;
    }
    const map: Record<string, string> = {
      concept_mixup: "Concept mix-up",
      formula_mixup: "Formula / calculation",
      careless: "Careless reading",
      interpretation: "Interpretation",
      unanswered: "Unanswered",
      definition: "Definition",
      recall: "Recall",
    };
    return map[code] ?? code;
  }

  questionTypeLabel(qt: string | null | undefined): string {
    const k = (qt || "").trim().toLowerCase();
    if (!k) {
      return "";
    }
    const tr = this.locale === "tr";
    const map: Record<string, { en: string; tr: string }> = {
      definition: { en: "Definition", tr: "Tanım" },
      concept: { en: "Concept", tr: "Kavram" },
      comparison: { en: "Comparison", tr: "Karşılaştırma" },
      application: { en: "Application", tr: "Uygulama" },
    };
    const row = map[k];
    if (row) {
      return tr ? row.tr : row.en;
    }
    return qt || "";
  }

  /** Short coaching line for aggregated error-type patterns (Results-only; no Analysis tab needed). */
  errorTypeCoachingHint(code: string): string {
    const tr = this.locale === "tr";
    const hintsTr: Record<string, string> = {
      concept_mixup:
        "Benzer terimleri yan yana yazıp farklarını tek cümleyle özetle; seçeneklerde ‘isim benzer’ tuzaklarına dikkat et.",
      formula_mixup:
        "Her sembolün neyi temsil ettiğini sözlü olarak tekrar et; sayı yerine birim ve anlam üzerinden kontrol et.",
      careless:
        "Soru kökündeki ‘değil’, ‘hangisi yanlış’, ‘en iyi’ gibi anahtar kelimeleri işaretle; cevabı göndermeden 5 sn geri oku.",
      interpretation:
        "Metinden çıkarım yaparken doğrudan alıntı ile gerekçeni eşleştir; korelasyon ≠ nedensellik gibi tuzakları özellikle ayır.",
      unanswered: "Zaman yetmediyse önce kolay soruları işaretle; boş bırakmak yerine mantıklı eleme yap.",
    };
    const hintsEn: Record<string, string> = {
      concept_mixup:
        "Write the similar terms side by side and state the difference in one sentence; watch for ‘similar name’ distractors.",
      formula_mixup:
        "Explain each symbol in words before calculating; sanity-check meaning and units, not just the final number.",
      careless:
        "Underline negations and qualifiers (‘NOT’, ‘least’, ‘best fit’); do a 5-second reread before submitting.",
      interpretation:
        "Tie each inference to a specific phrase in the material; explicitly separate correlation vs causation traps.",
      unanswered: "If time is tight, mark easy items first; use elimination instead of leaving items blank.",
    };
    const h = tr ? hintsTr : hintsEn;
    return h[code] ?? (tr ? "Bu tür hatalarda yavaşlayıp kökteki kuralı netleştir." : "Slow down and restate the underlying rule in your own words.");
  }

  /** Confused concepts frequency across wrong (non-blank) answers. */
  confusedConceptCounts(): { name: string; count: number }[] {
    const m = new Map<string, { name: string; count: number }>();
    for (const w of this.wrongFeedbackList()) {
      for (const c of w.confused_concepts || []) {
        const n = String(c || "").trim();
        if (!n) {
          continue;
        }
        const k = n.toLowerCase();
        const cur = m.get(k) || { name: n, count: 0 };
        cur.count += 1;
        if (!m.has(k)) {
          cur.name = n;
        }
        m.set(k, cur);
      }
    }
    return [...m.values()].sort((a, b) => b.count - a.count);
  }

  /** Wrong answers grouped by question_type (definition vs interpretation style). */
  wrongCountByQuestionType(): { typeKey: string; label: string; count: number }[] {
    const m = new Map<string, number>();
    for (const w of this.wrongFeedbackList()) {
      const raw = (w.question_type || "").trim().toLowerCase() || "unknown";
      m.set(raw, (m.get(raw) || 0) + 1);
    }
    return [...m.entries()]
      .map(([typeKey, count]) => ({
        typeKey,
        label: typeKey === "unknown" ? (this.locale === "tr" ? "Genel" : "General") : this.questionTypeLabel(typeKey),
        count,
      }))
      .sort((a, b) => b.count - a.count);
  }

  /**
   * Three-band weakness map: weak &lt;50%, medium 50–79%, good ≥80%.
   * Uses server topic_analysis when present, else derives from question_feedback.
   */
  weaknessMapBands(): { weak: { topic: string; pct: number }[]; medium: { topic: string; pct: number }[]; good: { topic: string; pct: number }[] } {
    const lines = this.topicSuccessTextLines();
    const weak: { topic: string; pct: number }[] = [];
    const medium: { topic: string; pct: number }[] = [];
    const good: { topic: string; pct: number }[] = [];
    for (const row of lines) {
      const item = { topic: row.topic, pct: row.pct };
      if (row.pct < 50) {
        weak.push(item);
      } else if (row.pct < 80) {
        medium.push(item);
      } else {
        good.push(item);
      }
    }
    const byPct = (a: { topic: string; pct: number }, b: { topic: string; pct: number }) => a.pct - b.pct;
    weak.sort(byPct);
    medium.sort(byPct);
    good.sort((a, b) => b.pct - a.pct);
    return { weak, medium, good };
  }

  weaknessBandTitle(band: "weak" | "medium" | "good"): string {
    const tr = this.locale === "tr";
    if (band === "weak") {
      return tr ? "Zayıf (<50%)" : "Weak (<50%)";
    }
    if (band === "medium") {
      return tr ? "Orta (50–79%)" : "Developing (50–79%)";
    }
    return tr ? "İyi (80%+)" : "Strong (80%+)";
  }

  showWeaknessMap(): boolean {
    const b = this.weaknessMapBands();
    return b.weak.length + b.medium.length + b.good.length > 0;
  }

  /** Aggregate “why wrong” narrative for Results (not only per-card). */
  whyWrongInsightBlock(): { show: boolean; headline: string; bullets: string[] } {
    const tr = this.locale === "tr";
    const wrong = this.wrongFeedbackList();
    const concepts = this.confusedConceptCounts();
    const byQType = this.wrongCountByQuestionType();
    const errEntries = this.errorSummaryEntries().filter((e) => e.key !== "unanswered");

    if (!wrong.length && !concepts.length && !errEntries.length) {
      return { show: false, headline: "", bullets: [] };
    }

    let headline: string;
    if (concepts.length > 0) {
      headline = tr
        ? "Yanlışların çoğu belirli kavramların birbirine karışması veya yanlış yorumlanmasıyla ilgili görünüyor."
        : "Most misses look tied to specific confusions or misreadings—not random guessing.";
    } else if (errEntries.length > 0) {
      headline = tr
        ? "Hata türlerine göre bakınca nerede hızlandığın veya hangi tuzağa düştüğün daha net."
        : "Grouped by mistake type, you can see where you rushed or which trap showed up most.";
    } else {
      headline = tr
        ? "Soru tiplerine göre yanlış dağılımın aşağıda — tanım mı, çıkarım mı daha çok zorladı, kontrol et."
        : "Below is how your misses split by question style—definitions vs. interpretation vs. comparisons.";
    }

    const bullets: string[] = [];

    if (concepts.length) {
      const top = concepts.slice(0, 4).map((c) => `${c.name} (${c.count}×)`);
      bullets.push(
        tr
          ? `Karıştırılan / netleştirilmesi gereken kavramlar: ${top.join(", ")}.`
          : `Concepts to untangle (frequency): ${top.join(", ")}.`,
      );
    }

    for (const row of byQType.slice(0, 3)) {
      if (row.typeKey === "unknown" && byQType.length === 1) {
        continue;
      }
      bullets.push(
        tr
          ? `Soru tipi açısından: “${row.label}” tarzında ${row.count} yanlış — tanımı ezberlemek ile metinden çıkarım yapmayı karıştırmamaya dikkat et.`
          : `By question style: ${row.count} miss(es) on “${row.label}” items — separate memorizing definitions from interpreting the text.`,
      );
    }

    for (const e of errEntries.slice(0, 4)) {
      const lab = this.errorTypeLabel(e.key);
      const hint = this.errorTypeCoachingHint(e.key);
      bullets.push(tr ? `“${lab}” (${e.count}): ${hint}` : `${lab} (${e.count}×): ${hint}`);
    }

    if (!bullets.length && wrong.length) {
      bullets.push(
        tr
          ? `${wrong.length} soruda cevabın doğru anahtarla örtüşmedi. Aşağıdaki kartlardaki “Neden yanlış” ve hata tipi rozetlerini sırayla oku.`
          : `${wrong.length} item(s) missed the correct key. Read each card’s “why wrong” line and the error-type badge in order.`,
      );
    }

    const lb = this.learningBrief;
    if (lb?.error_pattern_summary) {
      const s = String(lb.error_pattern_summary).trim();
      if (s) {
        bullets.unshift(tr ? `Özet: ${s}` : `Pattern: ${s}`);
      }
    }

    return { show: true, headline, bullets: bullets.slice(0, 8) };
  }

  /** Prefer specific lines built from this attempt; fall back to server strings. */
  personalizedRecommendationLines(): string[] {
    const tr = this.locale === "tr";
    const out: string[] = [];
    const seen = new Set<string>();

    const push = (s: string) => {
      const t = s.trim();
      const k = t.toLowerCase();
      if (t.length < 8 || seen.has(k)) {
        return;
      }
      seen.add(k);
      out.push(t);
    };

    const lb = this.learningBrief;
    for (const x of lb?.what_to_do_next || []) {
      push(String(x));
    }

    const concepts = this.confusedConceptCounts();
    const topConcept = concepts[0]?.name;
    const topErr = this.errorSummaryEntries().find((e) => e.key !== "unanswered");
    if (topConcept && topErr) {
      push(
        tr
          ? `${this.errorTypeLabel(topErr.key)} hatası “${topConcept}” ile ilişkili görünüyor; önce bunu tek paragrafta kendi cümlelerinle özetle, sonra 3 soruluk mini quiz yap.`
          : `Your ${this.errorTypeLabel(topErr.key)} misses cluster around “${topConcept}”—explain it in one paragraph in your own words, then run a 3-question micro-quiz.`,
      );
    } else if (topConcept) {
      push(
        tr
          ? `“${topConcept}” üzerinde dur: yanlış cevaplarda tekrar geçiyor; PDF asistanına “${topConcept} ile X farkı nedir?” diye sor.`
          : `Double down on “${topConcept}” (it repeats across misses). Ask the assistant: “How is ${topConcept} different from …?”`,
      );
    }

    const weakFirst = this.weaknessMapBands().weak[0];
    if (weakFirst) {
      push(
        tr
          ? `“${weakFirst.topic}” konusunda başarı %${weakFirst.pct}; bugünkü Pomodoro’yu bu konuya ayır, ardından zayıf konu mini quizi çöz.`
          : `“${weakFirst.topic}” is at ${weakFirst.pct}% today—do your Pomodoro block on that topic, then a weak-topic micro-quiz.`,
      );
    }

    for (const a of this.quizSubmission?.follow_up_actions || []) {
      push(String(a));
    }
    for (const t of this.quizSubmission?.teach_back_tasks || []) {
      push(String(t));
    }

    if (out.length < 2) {
      const pack = this.coachingPack;
      if (pack?.overall_next_steps) {
        push(String(pack.overall_next_steps));
      }
    }

    if (out.length < 2) {
      push(this.adaptiveFallbackWhatToDo());
    }

    return out.slice(0, 8);
  }

  studyPlanItemChecked: boolean[] = [];

  dailyStudyPlanItems(): {
    title: string;
    detail: string;
    action: "pomodoro" | "mini_quiz" | "mistakes" | "none";
    pomodoroTopic: string | null;
  }[] {
    const tr = this.locale === "tr";
    const bands = this.weaknessMapBands();
    const focusTopic =
      bands.weak[0]?.topic ||
      this.quizSubmission?.suggested_mini_quiz_topic?.trim() ||
      this.quizSubmission?.confused_topics_ranked?.[0] ||
      (this.wrongFeedbackList()[0]?.topic || "").trim() ||
      null;
    const topicLabel = focusTopic || (tr ? "zayıf konu" : "your weakest topic");
    const secondTopic =
      bands.weak[1]?.topic ||
      this.quizSubmission?.confused_topics_ranked?.[1] ||
      (this.wrongFeedbackList()[1]?.topic || "").trim() ||
      null;

    return [
      {
        title: tr ? "Pomodoro — kavram netliği" : "Pomodoro — concept clarity",
        detail: tr
          ? `25 dk: “${topicLabel}” için tanım vs. uygulama ayrımını yaz; PDF’ten 2 kısa alıntıyla destekle.`
          : `25 min: rewrite definitions vs. when-to-apply for “${topicLabel}”, supported by two short quotes from your PDF.`,
        action: "pomodoro",
        pomodoroTopic: focusTopic,
      },
      {
        title: tr ? "Hedefli mini quiz" : "Targeted micro-quiz",
        detail: tr
          ? `3–5 soru: yanlış yaptığın tiplere (özellikle ${topicLabel}${secondTopic ? ", " + secondTopic : ""}) odaklan.`
          : `3–5 questions: bias toward the mistake types you just saw (esp. ${topicLabel}${secondTopic ? ", " + secondTopic : ""}).`,
        action: "mini_quiz",
        pomodoroTopic: null,
      },
      {
        title: tr ? "Yanlışları tekrar" : "Mistakes review",
        detail: tr
          ? `10 dk: “Öğren yanlışlardan” bölümündeki kökleri sesli tekrar et; her madde için tek cümlelik düzeltme yaz.`
          : `10 min: re-walk “Learn from mistakes” stems aloud; write one correction sentence per item.`,
        action: "mistakes",
        pomodoroTopic: null,
      },
      {
        title: tr ? "Aralıklı tekrar" : "Spaced recap",
        detail: tr
          ? `Yarın 5 dk: bugünün zayıf etiketlerini tekrar oku; mümkünse aynı PDF oturumunda tekrar quiz.`
          : `Tomorrow (5 min): reread today’s weak labels; re-quiz in the same PDF session if you can.`,
        action: "none",
        pomodoroTopic: null,
      },
    ];
  }

  syncStudyPlanChecks(): void {
    const n = this.dailyStudyPlanItems().length;
    if (this.studyPlanItemChecked.length !== n) {
      this.studyPlanItemChecked = Array.from({ length: n }, () => false);
    }
  }

  toggleStudyPlanItem(i: number): void {
    this.syncStudyPlanChecks();
    if (i >= 0 && i < this.studyPlanItemChecked.length) {
      this.studyPlanItemChecked[i] = !this.studyPlanItemChecked[i];
    }
  }

  onStudyPlanMiniQuiz(): void {
    this.emitSuggestedMiniQuizCount();
  }

  onStudyPlanMistakesReview(): void {
    if (this.wrongFeedbackList().length) {
      this.startMistakesQuiz.emit();
    }
  }

  weakTopicSet(): Set<string> {
    const ta = this.topicAnalysis;
    const s = new Set<string>();
    for (const x of ta?.topics_under_half || []) {
      const k = String(x || "").trim().toLowerCase();
      if (k) {
        s.add(k);
      }
    }
    for (const tp of ta?.topics || []) {
      if ((tp.success_rate ?? 0) < 0.5 || tp.is_weak_under_half) {
        const k = (tp.topic || "").trim().toLowerCase();
        if (k) {
          s.add(k);
        }
      }
    }
    for (const x of [...(ta?.very_weak_topics || []), ...(ta?.weak_topics || [])]) {
      const k = String(x || "").trim().toLowerCase();
      if (k) {
        s.add(k);
      }
    }
    return s;
  }

  isWeakTopicName(name: string): boolean {
    return this.weakTopicSet().has((name || "").toLowerCase());
  }

  progressLine(p: TopicProgressItem): string {
    const pct = (r: number) => Math.round(r * 100);
    if (p.trend === "new" || p.previous_success_rate == null) {
      return `New topic this quiz: ${pct(p.current_success_rate)}%`;
    }
    const prev = pct(p.previous_success_rate);
    const cur = pct(p.current_success_rate);
    if (p.trend === "improved" && p.delta_points != null) {
      return `${p.topic}: improved from ${prev}% to ${cur}% (+${p.delta_points} pts)`;
    }
    if (p.trend === "declined" && p.delta_points != null) {
      return `${p.topic}: slipped from ${prev}% to ${cur}% (${p.delta_points} pts)`;
    }
    return `${p.topic}: stable around ${cur}%`;
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes["topicAnalysis"] || changes["quizSubmission"]) {
      this.rebuildCharts();
    }
    if (changes["quizSubmission"]) {
      this.studyPlanItemChecked = [];
      this.syncStudyPlanChecks();
      this.mistakeCarouselIndex = 0;
      this.mistakeReadPhase = 0;
      this.showInsightExpanded = false;
      this.showPlanExpanded = false;
      this.showBreakdownExpanded = false;
    }
    if (changes["quizSubmission"]?.currentValue && !environment.production) {
      const sub = changes["quizSubmission"].currentValue as QuizSubmissionResponse;
      const rows = extractWrongAnswers(sub);
      const tw = sub.total_wrong ?? 0;
      const listed = sub.listed_wrong_count;
      if (tw !== rows.length || (listed != null && listed !== rows.length)) {
        console.warn("[StudyFlow] wrong count vs mistake cards mismatch", {
          total_wrong: tw,
          listed_wrong_count: listed,
          renderedWrongCards: rows.length,
        });
      }
    }
  }

  /** Spinner only when parent is fetching and we have no submission/score to show. */
  get showBlockingLoader(): boolean {
    return this.resultsLoading && !this.lastQuizSummary && !this.quizSubmission;
  }

  get hasResultsShell(): boolean {
    return !!(
      this.lastQuizSummary ||
      this.quizSubmission ||
      (this.topicAnalysis?.topics?.length ?? 0) > 0
    );
  }

  get hasTopicRows(): boolean {
    return (this.topicAnalysis?.topics?.length ?? 0) > 0;
  }

  /** When the bar chart has no labels, show topic → % as plain text. */
  topicSuccessTextLines(): { topic: string; pct: number }[] {
    const ta = this.topicAnalysis;
    if (ta?.topics?.length) {
      return ta.topics.map((t) => ({
        topic: t.topic || "—",
        pct: Math.round((t.success_rate || 0) * 100),
      }));
    }
    const fb = this.quizSubmission?.question_feedback;
    if (!fb?.length) {
      return [];
    }
    const byTopic = new Map<string, { correct: number; total: number }>();
    for (const q of fb) {
      const topic = (q.topic || "").trim() || "—";
      const cur = byTopic.get(topic) || { correct: 0, total: 0 };
      cur.total += 1;
      if (feedbackRowIsCorrect(q)) {
        cur.correct += 1;
      }
      byTopic.set(topic, cur);
    }
    return [...byTopic.entries()].map(([topic, { correct, total }]) => ({
      topic,
      pct: total ? Math.round((correct / total) * 100) : 0,
    }));
  }

  noTopicAnalysisMessage(): string {
    return this.locale === "tr" ? "Henüz analiz oluşturulamadı." : "Analysis could not be generated yet.";
  }

  adaptiveFallbackWeakTopics(): string[] {
    const ta = this.topicAnalysis;
    const fromBands = [...(ta?.very_weak_topics || []), ...(ta?.weak_topics || [])].map((x) => String(x || "").trim()).filter(Boolean);
    if (fromBands.length) {
      return [...new Set(fromBands)];
    }
    const ranked = this.quizSubmission?.confused_topics_ranked || [];
    if (ranked.length) {
      return ranked.map((x) => String(x || "").trim()).filter(Boolean).slice(0, 12);
    }
    const fromWrong = this.wrongFeedbackList().map((w) => (w.topic || "").trim()).filter(Boolean);
    if (fromWrong.length) {
      return [...new Set(fromWrong)].slice(0, 12);
    }
    const sug = this.suggestions?.weak_topics ?? [];
    return sug.map((x) => String(x || "").trim()).filter(Boolean).slice(0, 12);
  }

  adaptiveFallbackWhy(): string {
    const tr = this.locale === "tr";
    const weak = this.adaptiveFallbackWeakTopics();
    if (weak.length) {
      return tr
        ? "Bu konularda başarı oranın düşük veya yanlış sayın fazla; tekrar ve netleştirme gerekiyor."
        : "Success rate is low or you missed several items here — review and clarify these areas.";
    }
    if ((this.lastQuizSummary?.wrong ?? 0) > 0) {
      return tr
        ? "Yanlış cevapların var; benzer sorularda hata yapmamak için konuyu pekiştir."
        : "You have incorrect answers; strengthen the underlying ideas to avoid repeat mistakes.";
    }
    return tr
      ? "Genel olarak performansını korumak için düzenli tekrar faydalı olur."
      : "Short, focused review sessions help maintain what you already know.";
  }

  adaptiveFallbackWhatToDo(): string {
    const tr = this.locale === "tr";
    const actions = this.quizSubmission?.follow_up_actions;
    if (actions?.length) {
      return actions.slice(0, 4).join(" ");
    }
    const ap = this.suggestions?.adaptive_plan;
    if (ap?.next_quiz_focus?.length) {
      return tr
        ? `Öncelik: ${ap.next_quiz_focus.join(", ")}`
        : `Focus next: ${ap.next_quiz_focus.join(", ")}`;
    }
    return tr
      ? "PDF asistanında zayıf konulara sor, kısa bir mikro-quiz veya Pomodoro ile tekrar et."
      : "Use the PDF assistant on weak areas, then try a short micro-quiz or a Pomodoro review.";
  }

  hasRichAdaptivePlan(): boolean {
    const ap = this.suggestions?.adaptive_plan;
    return !!(
      ap &&
      (ap.next_step_title ||
        ap.next_step_why ||
        ap.narrative ||
        (ap.next_quiz_focus && ap.next_quiz_focus.length))
    );
  }

  showAdaptiveFallbackBlock(): boolean {
    return !this.hasRichAdaptivePlan();
  }

  hasAnyTopicBandBadges(): boolean {
    const ta = this.topicAnalysis;
    if (!ta) {
      return false;
    }
    return !!(
      ta.very_weak_topics?.length ||
      ta.weak_topics?.length ||
      ta.developing_topics?.length ||
      ta.moderate_topics?.length ||
      ta.good_topics?.length ||
      ta.strong_topics?.length
    );
  }

  private rebuildCharts(): void {
    const ta = this.topicAnalysis;
    if (!ta || !ta.topics) {
      this.topicChartData = { labels: [], datasets: [{ data: [], backgroundColor: "#2563eb" }] };
      this.distributionChartData = {
        labels: [],
        datasets: [{ data: [], backgroundColor: [] }],
      };
      return;
    }

    const labels = ta.topics.map((t) => t.topic);
    const values = ta.topics.map((t) => Math.round((t.success_rate || 0) * 100));
    const barColors = ta.topics.map((t) => {
      const s = t.status === "moderate" ? "developing" : t.status;
      if (s === "strong") return "#16a34a";
      if (s === "good") return "#2563eb";
      if (s === "developing") return "#f59e0b";
      if (s === "weak") return "#dc2626";
      if (s === "very_weak") return "#7f1d1d";
      return "#64748b";
    });
    this.topicChartData = {
      labels,
      datasets: [{ data: values, backgroundColor: barColors }],
    };

    const norm = (st: string) => (st === "moderate" ? "developing" : st);
    const vweak = ta.topics.filter((t) => norm(t.status) === "very_weak").length;
    const weak = ta.topics.filter((t) => norm(t.status) === "weak").length;
    const dev = ta.topics.filter((t) => norm(t.status) === "developing").length;
    const good = ta.topics.filter((t) => t.status === "good").length;
    const strong = ta.topics.filter((t) => t.status === "strong").length;
    const tr = this.locale === "tr";
    this.distributionChartData = {
      labels: tr
        ? ["Çok zayıf 0–40%", "Zayıf 40–60%", "Geliştir 60–75%", "İyi 75–90%", "Güçlü 90%+"]
        : ["Very weak 0–40%", "Weak 40–60%", "Developing 60–75%", "Good 75–90%", "Strong 90%+"],
      datasets: [
        {
          data: [vweak, weak, dev, good, strong],
          backgroundColor: ["#7f1d1d", "#dc2626", "#f59e0b", "#2563eb", "#16a34a"],
        },
      ],
    };
  }
}
