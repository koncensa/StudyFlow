import { QuizHistoryEntry, QuizKind } from "./quiz-history";
import { ProfileBadgeItem } from "../models/types";

export type AppNavKey =
  | "home"
  | "pdf"
  | "quiz"
  | "results"
  | "analysis"
  | "plan"
  | "pomo"
  | "stats"
  | "badges"
  | "profile";

export type BadgeActionTarget = "pdf" | "quiz" | "pomo" | "analysis" | "plan";

export interface BadgeActionItem {
  target: BadgeActionTarget;
  en: string;
  tr: string;
}

export interface BadgeSection {
  key: "starter" | "activity" | "performance";
  title: string;
  badges: ProfileBadgeItem[];
}

const BADGE_FALLBACK_ACTION: BadgeActionItem = {
  target: "quiz",
  en: "Complete your next quiz attempt.",
  tr: "Bir sonraki quiz denemeni tamamla.",
};

const BADGE_ACTIONS_BY_SLUG: Record<string, BadgeActionItem> = {
  first_pdf: { target: "pdf", en: "Upload your first PDF from Assistant.", tr: "Asistan'dan ilk PDF'ini yükle." },
  first_quiz: { target: "quiz", en: "Finish your first quiz.", tr: "İlk quizini tamamla." },
  focus: { target: "pomo", en: "Finish one Pomodoro session.", tr: "Bir Pomodoro oturumunu bitir." },
  quiz_rookie: { target: "quiz", en: "Complete 5 quizzes.", tr: "5 quiz tamamla." },
  quiz_marathon: { target: "quiz", en: "Complete 20 quizzes.", tr: "20 quiz tamamla." },
  deep_focus: { target: "pomo", en: "Complete 5 Pomodoro sessions.", tr: "5 Pomodoro oturumu tamamla." },
  focus_legend: { target: "pomo", en: "Complete 25 Pomodoro sessions.", tr: "25 Pomodoro oturumu tamamla." },
  sharp_mind: { target: "quiz", en: "Score 90%+ in a quiz.", tr: "Bir quizde %90+ skor yap." },
  comeback: { target: "quiz", en: "Beat your previous quiz score.", tr: "Önceki quiz skorunu geç." },
  weak_topic_crusher: { target: "analysis", en: "Improve one weak topic over time.", tr: "Zayıf bir konuyu zamanla geliştir." },
  consistent_learner: { target: "quiz", en: "Study consistently across days.", tr: "Farklı günlerde düzenli çalış." },
  smart_improver: { target: "analysis", en: "Apply advice and improve results.", tr: "Önerileri uygulayıp sonuçlarını geliştir." },
  planner: { target: "plan", en: "Create your first study plan.", tr: "İlk çalışma planını oluştur." },
};

const BADGE_CTA_LABELS: Record<BadgeActionTarget, { en: string; tr: string }> = {
  pdf: { en: "Open assistant", tr: "Asistana git" },
  quiz: { en: "Go to quiz", tr: "Quiz'e git" },
  pomo: { en: "Go to Pomodoro", tr: "Pomodoro'ya git" },
  analysis: { en: "Open analysis", tr: "Analize git" },
  plan: { en: "Open plan", tr: "Plana git" },
};

export function badgeActionForSlug(slugRaw: string | null | undefined): BadgeActionItem {
  const slug = String(slugRaw || "").trim().toLowerCase();
  return BADGE_ACTIONS_BY_SLUG[slug] || BADGE_FALLBACK_ACTION;
}

export function badgeActionCtaText(target: BadgeActionTarget, tr: boolean): string {
  const labels = BADGE_CTA_LABELS[target];
  return tr ? labels.tr : labels.en;
}

export function buildBadgeSections(allBadges: ProfileBadgeItem[]): BadgeSection[] {
  if (!allBadges.length) {
    return [];
  }
  const bySlug = new Map(allBadges.map((b) => [String(b.slug || "").trim().toLowerCase(), b] as const));
  const pick = (slugs: string[]): ProfileBadgeItem[] =>
    slugs
      .map((slug) => bySlug.get(slug))
      .filter((b): b is ProfileBadgeItem => !!b);
  return [
    {
      key: "starter",
      title: "STARTER",
      badges: pick(["first_pdf", "first_quiz", "focus"]),
    },
    {
      key: "activity",
      title: "ACTIVITY",
      badges: pick(["quiz_rookie", "quiz_marathon", "deep_focus", "focus_legend"]),
    },
    {
      key: "performance",
      title: "PERFORMANCE",
      badges: pick([
        "sharp_mind",
        "comeback",
        "weak_topic_crusher",
        "consistent_learner",
        "smart_improver",
        "planner",
      ]),
    },
  ];
}

export function badgeEarnedCountForSections(sections: BadgeSection[]): number {
  const visibleBadges = sections.reduce<ProfileBadgeItem[]>((acc, group) => acc.concat(group.badges || []), []);
  return visibleBadges.filter((b) => !!b.earned).length;
}

export function badgeTotalCountForSections(sections: BadgeSection[]): number {
  return sections.reduce((count, group) => count + (group.badges?.length || 0), 0);
}

export function badgeCompletionPercentForSections(sections: BadgeSection[]): number {
  const total = badgeTotalCountForSections(sections);
  if (!total) {
    return 0;
  }
  return Math.round((100 * badgeEarnedCountForSections(sections)) / total);
}

export function badgeGroupEarnedCountForSection(section: BadgeSection): number {
  return (section.badges || []).filter((b) => !!b.earned).length;
}

export function quizKindLabelText(kind: QuizKind, tr: boolean): string {
  if (kind === "mini_adaptive") {
    return "Mini quiz";
  }
  return tr ? "Normal quiz" : "Standard quiz";
}

export function quizSourceLabelText(source: QuizHistoryEntry["quizSource"], tr: boolean): string {
  if (source === "pdf_session") {
    return tr ? "PDF oturumu" : "PDF session";
  }
  if (source === "text_summary") {
    return tr ? "Not özeti" : "Notes summary";
  }
  return tr ? "Diğer" : "Other";
}

export function quizShortCommentText(score: number, tr: boolean): string {
  const s = Math.max(0, Math.round(Number(score) || 0));
  if (s >= 80) {
    return tr
      ? "İyi performans. Ana kavramları büyük oranda doğru uyguladın."
      : "Good performance. You understood most of the key concepts.";
  }
  if (s >= 60) {
    return tr
      ? "Fena değil, ancak bazı konularda biraz daha pratik gerekli."
      : "Decent result, but some topics need more practice.";
  }
  return tr
    ? "Temel konularda daha fazla tekrar yapman gerekiyor."
    : "You need more revision on the core topics.";
}

export function navClassFor(activeNav: AppNavKey, nav: AppNavKey): string {
  const base = "sf-navLink";
  if (activeNav === nav) {
    return `${base} sf-navLink--active`;
  }
  return base;
}

export function pageShellClassFor(activeNav: AppNavKey): string {
  if (activeNav === "pdf") {
    return "container-fluid px-2 px-sm-3 px-lg-4 pt-2 pb-0 sf-page--assistant";
  }
  if (activeNav === "results" || activeNav === "analysis" || activeNav === "plan") {
    return "container-fluid px-2 px-sm-3 px-lg-4 pt-2 pb-0 sf-page--results";
  }
  if (activeNav === "stats") {
    return "container-fluid px-2 px-sm-3 px-lg-4 pt-2 pb-4 sf-page--progress";
  }
  if (activeNav === "badges") {
    return "container-fluid px-2 px-sm-3 px-lg-4 pt-2 pb-0 sf-page--badges";
  }
  if (activeNav === "profile") {
    return "container-fluid px-2 px-sm-3 px-lg-4 pt-2 pb-4 sf-page--profile";
  }
  if (activeNav === "quiz") {
    return "container-fluid px-2 px-sm-3 px-lg-4 pt-2 pb-0 sf-page--scroll";
  }
  if (activeNav === "home") {
    return "container-fluid px-3 px-sm-4 px-lg-5 py-4 py-lg-5 sf-page--homeNatural";
  }
  return "container-fluid px-3 px-sm-4 px-lg-5 py-4 py-lg-5 sf-page--scroll";
}

export function quizNavClassFor(params: {
  activeNav: AppNavKey;
  isAuthenticated: boolean;
  quizReady: boolean;
  hasQuizSubmission: boolean;
}): string {
  const base = "sf-navLink";
  if (!params.isAuthenticated) {
    return params.activeNav === "quiz" ? `${base} sf-navLink--active` : base;
  }
  const solving = params.quizReady && !params.hasQuizSubmission && params.activeNav === "quiz";
  return solving ? `${base} sf-navLink--active` : `${base} sf-navLink--muted`;
}
