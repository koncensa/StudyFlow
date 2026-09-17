/**
 * Rotating status lines while the server generates a quiz (PDF → topics → questions).
 * Used by AppComponent progress interval during `generateQuizFromPdf`.
 */
export function quizGenerationPhaseMessage(startedAtMs: number, lang: "en" | "tr"): string {
  const elapsed = Math.max(0, Date.now() - startedAtMs);
  const tr = lang === "tr";
  const phases = tr
    ? [
        "PDF’den konu başlıkları taranıyor…",
        "Önemli bölümler seçiliyor…",
        "Sorular üretiliyor (biraz sürebilir)…",
        "Son kontroller yapılıyor…",
        "Neredeyse hazır…",
      ]
    : [
        "Scanning topics from your PDF…",
        "Selecting key sections…",
        "Generating questions (this can take a moment)…",
        "Running final checks…",
        "Almost ready…",
      ];
  const idx = Math.min(phases.length - 1, Math.floor(elapsed / 4000));
  return phases[idx];
}
