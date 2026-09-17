# svc: quiz_prompt_constants | tr: quiz üretimi için paylaşılan llm talimat parçaları (sistem prompt kuralları) / en: shared llm instruction fragments for quiz generation (system prompt rules)

# tr: sadece pdf/metin kaynağı — dış bilgi yasak / en: pdf/study-text only — no outside knowledge
PDF_SOURCE_ONLY_RULES = (
    "PDF / STUDY-TEXT ONLY — no outside knowledge:\n"
    "- Every question stem and every option must be directly supported by the provided study text; "
    "do not invent facts, names, numbers, or formulas that do not appear there.\n"
    "- Prefer paraphrasing ideas from the text; distractors should be plausible misreadings of the same material, "
    "not random trivia.\n"
    "- In one quiz response, do not repeat the same option sentence on another question — every line A–D must be unique across all N items.\n"
)

# tr: tutarlı sınav tonu ve benzersiz soru kuralları / en: consistent exam tone and unique-question rules
QUIZ_TONE_UNIFIED = (
    "TONE — one consistent exam voice across the whole quiz (all N stems and all options):\n"
    "- Same language end-to-end; neutral third person; do not mix «you», «we», and «one» across items.\n"
    "- Avoid boilerplate openers such as «According to the source text…», «Based on the passage above…», or «As stated in the document…»; "
    "ask a direct question that names the concept, method, or comparison.\n"
    "- **Deliberately vary** stem length and rhythm from item to item (definition vs short scenario vs comparison) so the set "
    "does not read as one copy-pasted template; each stem must still be a **complete**, clear exam question (no clipped wording).\n"
    "- Options in the same question: parallel grammar (e.g. all full statements or all short noun phrases — pick one style and stick to it for that question).\n"
    "UNIQUENESS — no repeated question:\n"
    "- Each stem must quiz a different idea or angle from the material; do not paraphrase the same stem twice.\n"
    "- If you are unsure you have N distinct ideas, split the text into N non-overlapping subtopics before writing.\n"
)

# tr: stil, yazım, konu etiketi ve çıktı güvenilirliği kuralları / en: style, grammar, topic label, and output reliability rules
CONCISE_NO_SYNTAX_RULES = (
    "STYLE — clear human prose (no JSON/code inside stems or options):\n"
    "- Each option: one clear claim in a **complete** sentence — natural length; do not truncate or split words to stay under an artificial character limit.\n"
    "- Options (and short explanations): **no filler openers** — start with the substantive claim. "
    "Forbidden empty scaffolding in English: «According to the text…», «Based on the passage…», «It is important to note that…», "
    "«In this context…», «Basically / Actually / Essentially, …» as throat-clearing. "
    "In Turkish: no «Metne göre…», «Belirtmek gerekir ki…», «Bu bağlamda…», «Öncelikle…» as hollow prefaces when they add no content.\n"
    "- No code-like syntax, no markdown fences, no list-of-JSON fragments inside options.\n"
    "- No outline or list prefixes on stems or options: do not start with «1)», «1,», «2.», «12.», «•», a dash bullet, "
    "«(1)», a bare «.», or «. veya …» — only a plain exam sentence (options are already ordered A–D by the UI; "
    "never prefix the option text itself with list numbers or bullets).\n"
    "- Use normal letters, digits, and basic punctuation only in stems and options: no emoji, no pictographs, "
    "no invisible Unicode (zero-width spaces, Bidi controls), and no private-use «tofu» glyphs.\n"
    "- No broken grammar pairs such as «has is», «are is», «were was», «will would»; no unmatched parentheses or quotation marks; "
    "do not paste the same clause twice inside one option.\n"
    "- Every stem and option must be a **finished** sentence or noun phrase: no trailing comma, no stem ending on "
    "«because / that / which / çünkü / için / eğer» with nothing after it, and no cut-off ending on a dash.\n"
    "PRINT / PDF — professional, exam-ready wording:\n"
    "- Proofread stems and options: no doubled words (e.g. «for for»), no accidental spaces inside a word, "
    "no garbled half-clauses or notebook paste.\n"
    "- Prefer parallel structure across the four options (same tense; each option one clear claim).\n"
    "- Do not paste raw training logs, fit/print output, or «$… ModelCV: $…» metric dumps as an option unless "
    "the stem explicitly refers to interpreting that exact line from the document.\n"
    "- Explain methods in plain language — no Python/JS loops, assignments, constructors, or library calls in stems or options.\n"
    "TEACHING VALUE (student must learn something usable in print/PDF):\n"
    "- Each option = one clear claim in normal sentences; never repeat the same long clause twice inside one option.\n"
    "- Avoid tautologies («For topic X … the accurate interpretation is that X» with no new information).\n"
    "- No LaTeX, no display-math dumps, and no chains of Σ/∑/∫/∏, stacked subscripts, or mangled «^y_i» / «|w_j|» notation — "
    "if the PDF has a formula, restate its meaning in words unless it is one very short expression copied exactly.\n"
    "- Comparison stems: compare two short concept names (a few words each), not two pasted section titles or comma-heavy fragments.\n"
    "TOPIC LABEL (metadata only, also shown in the UI):\n"
    "- 2–6 words, Title Case or sentence case; a real concept name — never a slide title ending in «This», «We create», or «…on».\n"
    "- Never start with «Part A —» / «Part B —»; never paste «In [12]:» cell markers or «# What does …» lines into topic or options.\n"
    "- Never give away where something sits in a workbook (no «Part B», «Scenario:», «Exercise 3»); the quiz should read like a clean exam, not a table of contents.\n"
    "OUTPUT RELIABILITY:\n"
    "- If the task asks for exactly N questions, return exactly N array entries — no empty shells, no «TBD», no duplicate stems.\n"
    "- Each entry: four distinct non-empty options; correct_answer must exactly match one option string (verbatim).\n"
    "WITHIN-QUIZ COHERENCE (no recycled junk across questions):\n"
    "- Across the whole quiz, do not reuse the same option text (or a near-copy) on different questions; each distractor should be fresh for that stem.\n"
    "- For one stem, all four options must be on the same subtopic as the question — do not paste unrelated lines (house counts, loan default blurbs, Train R²/Test RMSE tables, unrelated metric lists) as fillers.\n"
    "- Never use the bare word «Problem» as a concept name in a comparison stem; use the real second method or notion from the text.\n"
    "- Topic labels and stems must read as real concept names, not fragments like «10 Evaluation» torn from a numbered list — rewrite in plain English.\n"
)

# tr: soru kökü ve şık çeşitliliği kuralları / en: stem and option diversity rules
QUIZ_DIVERSITY_RULES = (
    "VARIETY — stems and options must not blur together:\n"
    "- Across several questions in one response (or when you imagine a longer quiz), do **not** open every stem with the same stock phrase "
    "(e.g. repeating only «Which statement…» / «Which of the following…»). Match opening pattern to the task: definition vs comparison vs "
    "application should **read** like different question types, not copy-paste with one word swapped.\n"
    "- Within one question, each of the four options must state a **different substantive claim** about the topic. "
    "Forbidden lazy pattern: one correct sentence plus three near-copies that only negate it, flip «is/is not», swap one adverb, "
    "or reuse the same clause skeleton with a tiny edit — each distractor must be wrong for a **distinct** reason.\n"
    "- Parallel grammar across the four options is good (same tense, same length band), but subject–verb–object **content** must differ clearly; "
    "do not output four lines that differ by a single word or punctuation mark.\n"
)

# tr: şablon tekrarını ve yazım hatalarını engelle / en: prevent template repetition and typos
QUIZ_ANTI_SAMENESS_RULES = (
    "ANTI-TEMPLATE — the whole quiz must read as a human exam, not one slot repeated N times:\n"
    "- Do **not** start two different questions with the same first five words (e.g. multiple stems opening with "
    "«Which of the following…», «Which statement…», «Aşağıdakilerden hangisi…», or «Hangisi doğrudur…»).\n"
    "- Rotate stem **shapes**: direct wh-questions, short context + «what should…», «least accurate / en yanlış ifade», "
    "«best applies when…», comparison of two named ideas, and one «spot the error in this reasoning» style when the text allows it.\n"
    "- Avoid serial stems that only swap one noun inside an otherwise identical clause — rewrite the whole stem angle.\n"
    "SYNTAX & TYPO — zero tolerance for sloppy surface form:\n"
    "- Proofread each stem and option before output: subject–verb agreement, correct auxiliaries, no duplicated words "
    "(«the the», «için için»), no stray double punctuation (`..`, `??`, `,,`), no broken pairs («is are», «was were» mixed).\n"
    "- Turkish: complete sentences; avoid comma splices; question particle harmony (`mı/mi/mu/mü`) must match the stressed vowel class "
    "of the last word before it; never stack duplicate particles (`… mi mi`).\n"
    "- English: no sentence fragments as stems unless the stem is a clear directed prompt; matching parentheses and quotes.\n"
    "- Stems that ask a question should end with «?»; each option is a normal declarative sentence ending with a period where appropriate.\n"
)

# tr: tüm mcq kurallarının birleşimi — quiz_service sistem prompt'una eklenir / en: combined mcq rules — appended to quiz_service system prompts
QUIZ_MCQ_SYSTEM_RULES = (
    PDF_SOURCE_ONLY_RULES + CONCISE_NO_SYNTAX_RULES + QUIZ_DIVERSITY_RULES + QUIZ_ANTI_SAMENESS_RULES + QUIZ_TONE_UNIFIED
)
