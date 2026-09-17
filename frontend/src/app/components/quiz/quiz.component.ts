// cmp: quiz | tr: quiz ekranı — sorular, cevap, gönder, geri bildirim / en: quiz screen questions answers submit feedback

import {
  Component,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
} from "@angular/core";

import { AppLang } from "../../i18n/app-lang";
import {
  QuizLearningBrief,
  QuizQuestion,
  QuizQuestionFeedback,
  QuizSubmissionResponse,
  QuizSubmitPayload,
  SuggestionResponse,
  TailoredMiniQuizPayload,
} from "../../models/types";
import { extractWrongAnswers, remedialTopicNames } from "../../utils/quiz-results-pipeline";
import { quizScorePercentFromAttempt } from "../../utils/quiz-score-display";
import { feedbackRowIsCorrect, feedbackRowIsUnanswered } from "../../utils/quiz-submission-normalize";

@Component({
  selector: "app-quiz",
  templateUrl: "./quiz.component.html",
  styleUrls: ["./quiz.component.css"],
})
export class QuizComponent implements OnChanges, OnDestroy {
  @Input() questions: QuizQuestion[] | null = null;
  @Input() loading = false;
  @Input() submission: QuizSubmissionResponse | null = null;
  /** Filled after submit — used for coach line + plan preview on the quiz tab. */
  @Input() suggestions: SuggestionResponse | null = null;
  /** From backend: adaptive time limit (seconds). 0 = no timer. */
  @Input() timeLimitSeconds = 0;
  @Input() locale: AppLang = "en";

  @Output() submit = new EventEmitter<QuizSubmitPayload>();
  /** Fired when countdown hits zero — parent should submit same as `submit`. */
  @Output() timeUp = new EventEmitter<QuizSubmitPayload>();
  /** After results: mini quiz with topic focus (same PDF session). */
  @Output() postMiniQuiz = new EventEmitter<TailoredMiniQuizPayload>();
  @Output() postOpenAssistant = new EventEmitter<void>();
  @Output() postRetryQuiz = new EventEmitter<void>();
  /** Quiz sonrası analiz sekmesine geçiş. */
  @Output() postOpenAnalysis = new EventEmitter<void>();

  /**
   * Selected option text per question slot. Keys must be unique per array index — duplicate
   * backend `id` values would otherwise collapse into one map entry and break submit payloads.
   */
  answersByQuestionId: Record<string, string> = {};
  currentIndex = 0;
  remainingSeconds = 0;
  private timerId: ReturnType<typeof setInterval> | null = null;
  private timedOut = false;
  activeMistakeIndex = 0;
  /** Wall clock from when this question set became active (for duration reporting). */
  private quizWallClockStartedAt = 0;

  ngOnChanges(changes: SimpleChanges): void {
    if (changes["questions"]) {
      this.resetForNewQuestions();
    }
    const subCh = changes["submission"];
    if (subCh && subCh.currentValue) {
      this.clearTimer();
      this.activeMistakeIndex = 0;
    }
  }

  ngOnDestroy(): void {
    this.clearTimer();
  }

  private resetForNewQuestions(): void {
    this.timedOut = false;
    this.clearTimer();
    const qs = this.questions || [];
    this.quizWallClockStartedAt = qs.length ? Date.now() : 0;
    const next: Record<string, string> = {};
    for (let i = 0; i < qs.length; i++) {
      next[this.questionKey(qs[i], i)] = "";
    }
    this.answersByQuestionId = next;
    this.currentIndex = 0;
    const fromApi = Math.max(0, Math.round(this.timeLimitSeconds || 0));
    const n = qs.length;
    const fallback = Math.min(45 * 60, Math.max(3 * 60, n * 75));
    const limit = fromApi > 0 ? fromApi : fallback;
    if (qs.length > 0 && limit > 0) {
      this.remainingSeconds = limit;
      this.startTimer();
    } else {
      this.remainingSeconds = 0;
    }
  }

  private startTimer(): void {
    this.clearTimer();
    this.timerId = setInterval(() => {
      this.remainingSeconds = Math.max(0, this.remainingSeconds - 1);
      if (this.remainingSeconds <= 0) {
        this.clearTimer();
        this.emitTimeUp();
      }
    }, 1000);
  }

  private clearTimer(): void {
    if (this.timerId != null) {
      clearInterval(this.timerId);
      this.timerId = null;
    }
  }

  private emitTimeUp(): void {
    if (this.timedOut || !this.questions?.length || this.submission || this.loading) {
      return;
    }
    this.timedOut = true;
    this.timeUp.emit(this.buildSubmitPayload());
  }

  get total(): number {
    return this.questions?.length || 0;
  }

  get currentQuestion(): QuizQuestion | null {
    const qs = this.questions;
    if (!qs || !qs.length) {
      return null;
    }
    return qs[this.currentIndex] || null;
  }

  /** One slot per question index (never rely on id alone — duplicates break multi-question quizzes). */
  questionKey(_q: QuizQuestion, index: number): string {
    return `slot:${index}`;
  }

  get currentHasSelection(): boolean {
    const q = this.currentQuestion;
    if (!q || !this.questions?.length) {
      return false;
    }
    return !!((this.answersByQuestionId[this.questionKey(q, this.currentIndex)] || "").trim());
  }

  get progressLabel(): string {
    return `${this.currentIndex + 1} / ${this.total}`;
  }

  get progressPercent(): number {
    if (!this.total) {
      return 0;
    }
    return Math.round(((this.currentIndex + 1) / this.total) * 100);
  }

  get timerDisplay(): string {
    if (this.remainingSeconds <= 0) {
      return "—";
    }
    const m = Math.floor(this.remainingSeconds / 60);
    const s = this.remainingSeconds % 60;
    return `${m}:${s < 10 ? "0" : ""}${s}`;
  }

  get timerUrgent(): boolean {
    return this.remainingSeconds > 0 && this.remainingSeconds <= 60;
  }

  t(msg: string): string {
    return msg;
  }

  selectOption(opt: string): void {
    if (this.loading || this.submission || !this.questions?.length) {
      return;
    }
    const q = this.currentQuestion;
    if (!q) {
      return;
    }
    const key = this.questionKey(q, this.currentIndex);
    this.answersByQuestionId = { ...this.answersByQuestionId, [key]: opt };
  }

  isSelected(opt: string): boolean {
    const q = this.currentQuestion;
    if (!q || !this.questions?.length) {
      return false;
    }
    const key = this.questionKey(q, this.currentIndex);
    return (this.answersByQuestionId[key] || "") === opt;
  }

  trackOption(index: number, opt: string): string {
    const q = this.currentQuestion;
    const qk = q ? this.questionKey(q, this.currentIndex) : "_";
    return `${qk}::${index}::${opt}`;
  }

  canGoBack(): boolean {
    return this.currentIndex > 0 && !this.loading && !this.submission;
  }

  canGoNext(): boolean {
    if (!this.questions?.length || this.submission || this.loading) {
      return false;
    }
    return this.currentIndex < this.total - 1;
  }

  isLastQuestion(): boolean {
    return this.currentIndex >= this.total - 1;
  }

  goBack(): void {
    if (!this.canGoBack()) {
      return;
    }
    this.currentIndex--;
  }

  goNext(): void {
    if (!this.canGoNext()) {
      return;
    }
    this.currentIndex++;
  }

  canSubmit(): boolean {
    if (!this.questions || this.questions.length === 0) {
      return false;
    }
    if (this.loading || this.submission) {
      return false;
    }
    return this.isLastQuestion();
  }

  unansweredCount(): number {
    const qs = this.questions || [];
    return qs.filter((q, i) => !((this.answersByQuestionId[this.questionKey(q, i)] || "").trim())).length;
  }

  private buildSubmitPayload(): QuizSubmitPayload {
    const started = this.quizWallClockStartedAt || Date.now();
    const durationSeconds = Math.max(0, Math.round((Date.now() - started) / 1000));
    const qs = this.questions || [];
    const selectedAnswers = qs.map((q, i) => (this.answersByQuestionId[this.questionKey(q, i)] || "").trim());
    return {
      selectedAnswers,
      durationSeconds,
    };
  }

  onSubmitClick(): void {
    if (!this.canSubmit()) {
      return;
    }
    this.clearTimer();
    this.submit.emit(this.buildSubmitPayload());
  }

  optionLetter(i: number): string {
    return String.fromCharCode(65 + i);
  }

  get wrongFeedbacks(): QuizQuestionFeedback[] {
    const sub = this.submission;
    if (!sub) {
      return [];
    }
    const wi = sub.wrong_items;
    const tw = sub.total_wrong ?? 0;
    if (wi && wi.length === tw) {
      return wi;
    }
    const fb = sub.question_feedback;
    if (!fb?.length) {
      return [];
    }
    return fb.filter((x) => !feedbackRowIsCorrect(x) && !feedbackRowIsUnanswered(x));
  }

  get activeWrongFeedback(): QuizQuestionFeedback | null {
    const rows = this.wrongFeedbacks;
    if (!rows.length) {
      return null;
    }
    const idx = Math.max(0, Math.min(this.activeMistakeIndex, rows.length - 1));
    return rows[idx];
  }

  canGoPrevMistake(): boolean {
    return this.activeMistakeIndex > 0;
  }

  canGoNextMistake(): boolean {
    return this.activeMistakeIndex < this.wrongFeedbacks.length - 1;
  }

  goPrevMistake(): void {
    if (!this.canGoPrevMistake()) {
      return;
    }
    this.activeMistakeIndex -= 1;
  }

  goNextMistake(): void {
    if (!this.canGoNextMistake()) {
      return;
    }
    this.activeMistakeIndex += 1;
  }

  postMiniCount(): number {
    const raw = this.submission?.recommended_mini_quiz_count;
    const n = raw != null ? Number(raw) : NaN;
    if (Number.isFinite(n) && n >= 1) {
      return Math.min(15, Math.max(1, Math.round(n)));
    }
    return 3;
  }

  /** Single weakest topic for mini quiz (server hint → first very weak → first weak). */
  miniQuizFocusTopics(): string[] {
    const sub = this.submission;
    if (!sub) {
      return [];
    }
    const hinted = (sub.suggested_mini_quiz_topic || "").trim();
    if (hinted) {
      return [hinted];
    }
    const ta = sub.topic_analysis;
    const vw = (ta?.very_weak_topics || []).map((x) => String(x).trim()).filter(Boolean);
    if (vw.length) {
      return [vw[0]];
    }
    const wk = (ta?.weak_topics || []).map((x) => String(x).trim()).filter(Boolean);
    if (wk.length) {
      return [wk[0]];
    }
    const ranked = [...(sub.confused_topics_ranked || [])].map((x) => String(x).trim()).filter(Boolean);
    if (ranked.length) {
      return [ranked[0]];
    }
    const fromWrong = extractWrongAnswers(sub)
      .map((r) => (r.topic || "").trim())
      .filter(Boolean);
    if (fromWrong.length) {
      return [fromWrong[0]];
    }
    const rem = remedialTopicNames(ta ?? null);
    return rem.length ? [rem[0]] : [];
  }

  get learningBrief(): QuizLearningBrief | null {
    const s = this.submission;
    return s && s.learning_brief ? s.learning_brief : null;
  }

  get submissionUnansweredCount(): number {
    const n = this.submission?.total_unanswered;
    return n != null && Number.isFinite(n) ? Math.max(0, Math.round(n)) : 0;
  }

  get submissionDurationSeconds(): number {
    const d = this.submission?.total_duration_seconds;
    return d != null && Number.isFinite(d) ? Math.max(0, Math.round(d)) : 0;
  }

  /** Short numbered-style steps: coaching pack → learning brief → adaptive narrative → general actions. */
  planStepsAfterQuiz(): string[] {
    const pack = this.suggestions && this.suggestions.coaching_pack;
    const steps = pack && pack.study_plan_steps ? pack.study_plan_steps : [];
    if (steps.length) {
      return steps.slice(0, 6).map((x) => String(x).trim()).filter(Boolean);
    }
    const lb = this.learningBrief;
    if (lb && lb.what_to_do_next && lb.what_to_do_next.length) {
      return lb.what_to_do_next.slice(0, 6).map((x) => String(x).trim()).filter(Boolean);
    }
    const ap = this.suggestions && this.suggestions.adaptive_plan;
    const nar = ap && ap.narrative ? String(ap.narrative).trim() : "";
    if (nar) {
      return nar
        .split(/\n+/)
        .map((s) => s.trim())
        .filter(Boolean)
        .slice(0, 5);
    }
    const acts = this.suggestions && this.suggestions.general_actions;
    if (acts && acts.length) {
      return acts
        .map((a) => (a.message || "").trim())
        .filter(Boolean)
        .slice(0, 5);
    }
    return [];
  }

  coachLineAfterQuiz(): string | null {
    const cm = this.suggestions && this.suggestions.coach_message;
    if (cm != null && String(cm).trim()) {
      return String(cm).trim();
    }
    return null;
  }

  hasConfusedConcepts(w: QuizQuestionFeedback): boolean {
    return !!(w.confused_concepts && w.confused_concepts.length);
  }

  emitPostMiniQuiz(): void {
    const pct = this.submissionScorePercent();
    let difficulty: "beginner" | "normal" | "technical" = "normal";
    if (pct < 55) {
      difficulty = "beginner";
    } else if (pct >= 80) {
      difficulty = "technical";
    }
    const strong = (this.submission?.topic_analysis?.strong_topics || [])
      .concat(this.submission?.topic_analysis?.good_topics || [])
      .map((x) => String(x || "").trim())
      .filter(Boolean);
    const salt = `${this.miniQuizFocusTopics().join("|")}::${Date.now()}`;
    const challengeTopics = this.pickChallengeTopicsForVariety(strong, salt);
    this.postMiniQuiz.emit({
      count: this.postMiniCount(),
      topics: this.miniQuizFocusTopics(),
      difficulty,
      challengeTopics: challengeTopics.length ? challengeTopics : undefined,
    });
  }

  /** Aynı zayıf konuda bile soru çeşitlensin diye güçlü konulardan deterministik alt küme. */
  private pickChallengeTopicsForVariety(candidates: string[], salt: string): string[] {
    if (!candidates.length) {
      return [];
    }
    const scored = candidates.map((t) => ({ t, s: this.hash32(t + "::" + salt) }));
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

  private hash32(s: string): number {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
      h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
    }
    return h;
  }

  labelMiniQuiz(): string {
    return this.locale === "tr" ? "Mini quiz çöz" : "Take mini quiz";
  }

  labelGoTopic(): string {
    return this.locale === "tr" ? "Konuya git" : "Open assistant";
  }

  labelRetry(): string {
    return this.locale === "tr" ? "Tekrar dene" : "Try again";
  }

  labelOpenAnalysis(): string {
    return this.locale === "tr" ? "Analize geç" : "Go to analysis";
  }

  private submissionScorePercent(): number {
    const sub = this.submission;
    if (!sub) {
      return 0;
    }
    return quizScorePercentFromAttempt({
      total_correct: sub.total_correct,
      total_wrong: sub.total_wrong,
      total_unanswered: sub.total_unanswered,
      total_question_count: sub.total_question_count,
      question_feedback: sub.question_feedback,
      score_percentage: sub.score_percentage,
    });
  }

  /** Rounded 0–100 for bands, bar width, and mini-quiz hints. */
  scorePercentRounded(): number {
    return Math.max(0, Math.min(100, Math.round(this.submissionScorePercent())));
  }

  /** Same % as bands, one decimal for the hero stat (matches server rounding). */
  scorePercentDisplayDecimal(): number {
    return Math.max(0, Math.min(100, this.submissionScorePercent()));
  }

  resultBandLabel(): string {
    const tr = this.locale === "tr";
    const s = this.scorePercentRounded();
    if (s < 40) {
      return tr ? "Temel güçlendir (0–39%)" : "Foundation (0–39%)";
    }
    if (s < 55) {
      return tr ? "Gelişim başlangıcı (40–54%)" : "Early growth (40–54%)";
    }
    if (s < 70) {
      return tr ? "Gelişim bölgesi (55–69%)" : "Growth zone (55–69%)";
    }
    if (s < 85) {
      return tr ? "Sağlam ilerleme (70–84%)" : "Solid progress (70–84%)";
    }
    return tr ? "Güçlü performans (85%+)" : "Strong performance (85%+)";
  }

  resultBandClass(): string {
    const s = this.scorePercentRounded();
    if (s < 40) {
      return "qz-band qz-band--danger";
    }
    if (s < 55) {
      return "qz-band qz-band--warn";
    }
    if (s < 70) {
      return "qz-band qz-band--warn";
    }
    if (s < 85) {
      return "qz-band qz-band--good";
    }
    return "qz-band qz-band--great";
  }

  highlightedWeakTopics(): string[] {
    const out: string[] = [];
    const seen = new Set<string>();
    const push = (x: string) => {
      const txt = String(x || "").trim();
      const key = txt.toLowerCase();
      if (!txt || seen.has(key)) {
        return;
      }
      seen.add(key);
      out.push(txt);
    };
    const ta = this.submission?.topic_analysis;
    for (const x of ta?.very_weak_topics || []) {
      push(x);
    }
    for (const x of ta?.weak_topics || []) {
      push(x);
    }
    if (!out.length) {
      for (const x of this.submission?.confused_topics_ranked || []) {
        push(x);
      }
    }
    return out.slice(0, 4);
  }

  focusTopicLine(): string {
    const tr = this.locale === "tr";
    const top = this.highlightedWeakTopics()[0];
    if (top) {
      return tr
        ? `Hızlı kazançları açmak için önce "${top}" konusuna odaklan.`
        : `Focus on "${top}" first to unlock the fastest score improvement.`;
    }
    return tr
      ? "Belirgin zayıf konu yok. Formu korumak için mini quiz ile ritmi sürdür."
      : "No major weak topic detected. Keep momentum with a short mini quiz.";
  }

  /** One line per score band — always tied to the computed %, never a modulo “pick”. */
  quizResultMotivation(): string {
    const tr = this.locale === "tr";
    const s = this.scorePercentRounded();
    if (s < 40) {
      return tr
        ? "Skor düşük; önce zayıf konulara odaklanmak en hızlı toparlanma yoludur."
        : "Score is low — rebuilding weak topics first is the fastest way up.";
    }
    if (s < 55) {
      return tr
        ? "Temel fikirler oturuyor; birkaç net düzeltmeyle hızlı yükselirsin."
        : "Core ideas are there — a few sharp fixes will lift you quickly.";
    }
    if (s < 70) {
      return tr
        ? "İyi bir taban; ara bölgelerde pratikle üst bantlara çıkabilirsin."
        : "Solid base — short drills on mid topics usually push you into the next band.";
    }
    if (s < 85) {
      return tr
        ? "Güçlü performans; kalan bir iki başlığı mikro quiz ile kapat."
        : "Strong run — micro-quizzes on the remaining gaps will lock this in.";
    }
    return tr
      ? "Çok iyi; haftada bir karışık tekrar güçlü konuları taze tutar."
      : "Excellent — light mixed review weekly keeps strong topics automatic.";
  }

  mistakeWhyText(w: QuizQuestionFeedback): string {
    const txt = (w.why_incorrect || w.why_wrong || "").trim();
    if (txt) {
      return txt;
    }
    return this.locale === "tr"
      ? "Bu seçenek soru koşulunu tam karşılamıyor. Kökü tekrar okuyup doğru kavramı seç."
      : "This option does not satisfy the question condition. Re-read the stem and match the core concept.";
  }

  mistakeUserAnswerText(w: QuizQuestionFeedback): string {
    const selected = (w.selected_answer || w.user_answer || "").trim();
    if (selected) {
      return selected;
    }
    return this.locale === "tr" ? "(boş bırakıldı)" : "(left blank)";
  }

  personalizedActionCards(): {
    key: "mini_quiz" | "assistant" | "retry";
    title: string;
    detail: string;
    cta: string;
    style: "primary" | "secondary";
  }[] {
    const tr = this.locale === "tr";
    const weak = this.highlightedWeakTopics()[0];
    return [
      {
        key: "mini_quiz",
        title: tr ? "Zayıf konu pratiği" : "Practice weak topics",
        detail: weak
          ? tr
            ? `Önerilen odak: ${weak}. 3-4 soruluk hızlı tekrar ile pekiştir.`
            : `Recommended focus: ${weak}. Reinforce with a quick 3-4 question drill.`
          : tr
            ? "En zayıf banda odaklı mini quiz başlat."
            : "Start a mini quiz focused on your weakest band.",
        cta: this.labelMiniQuiz(),
        style: "primary",
      },
      {
        key: "assistant",
        title: tr ? "Açıklamayı gözden geçir" : "Review explanation",
        detail: weak
          ? tr
            ? `"${weak}" için PDF asistanına kısa bir fark analizi sor.`
            : `Ask the PDF assistant for a concise concept contrast on "${weak}".`
          : tr
            ? "Yanlış yaptığın soruların nedenini asistandan adım adım açıklamasını iste."
            : "Ask the assistant to explain why your wrong options failed, step by step.",
        cta: this.labelGoTopic(),
        style: "secondary",
      },
      {
        key: "retry",
        title: tr ? "Yeni deneme" : "Take mini quiz again",
        detail: tr
          ? "Aynı oturumda yeniden dene ve gelişimi karşılaştır."
          : "Retry in the same session and compare improvement immediately.",
        cta: this.labelRetry(),
        style: "secondary",
      },
    ];
  }

  runActionCard(key: "mini_quiz" | "assistant" | "retry"): void {
    if (key === "mini_quiz") {
      this.emitPostMiniQuiz();
      return;
    }
    if (key === "assistant") {
      this.postOpenAssistant.emit();
      return;
    }
    this.postRetryQuiz.emit();
  }

  errorTypeLabel(code: string | null | undefined): string {
    if (!code) {
      return "";
    }
    const tr = this.locale === "tr";
    const map: Record<string, string> = tr
      ? {
          concept_mixup: "Kavram karışması",
          formula_mixup: "Formül / hesap",
          careless: "Dikkat / okuma",
          interpretation: "Yorumlama",
          unanswered: "Boş",
        }
      : {
          concept_mixup: "Concept mix-up",
          formula_mixup: "Formula / calculation",
          careless: "Careless reading",
          interpretation: "Interpretation",
          unanswered: "Unanswered",
        };
    return map[code] ?? code;
  }
}
