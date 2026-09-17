# svc: quiz_rule_based | tr: llm yetersiz kalınca kural tabanlı quiz sorusu üret (yedek) / en: rule-based quiz generation when llm output is insufficient (fallback)

from __future__ import annotations

import hashlib
import random
import re
from typing import List, Optional, Sequence, Set, Tuple

from app.schemas.quiz_schema import QuizQuestion

from app.services.quiz_validation import (
    STOP_WORDS,
    TOPIC_QUESTION_TYPES,
    bad_option_text,
    clip_quiz_visible_text,
    normalize_quiz_options,
    normalize_session_difficulty,
)


# fn: split_into_sentences | tr: metni cümlelere böl / en: split text into sentences
def split_into_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


# fn: shorten_definition | tr: tanım metnini okunabilir uzunluğa kırp / en: clip definition text to readable length
def shorten_definition(text: str, max_len: int = 140) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = re.sub(r"^[\-:;,.\s]+", "", t)
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    for sep in (". ", "; ", ", "):
        p = cut.rfind(sep)
        if p >= 40:
            return cut[:p].strip()
    return clip_quiz_visible_text(t, max_len).strip()


# fn: extract_concept_pairs | tr: metinden kavram–tanım çiftleri çıkar (heuristik) / en: extract concept–definition pairs from text (heuristic)
def extract_concept_pairs(text: str, max_pairs: int = 30) -> List[tuple[str, str]]:
    pairs: List[tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()

    def add_pair(concept: str, definition: str) -> None:
        concept = re.sub(r"\s{2,}", " ", concept.strip())
        definition = shorten_definition(definition.strip(), 160)
        if len(concept) < 2 or len(concept) > 72 or len(definition) < 18:
            return
        if concept.lower() in STOP_WORDS or len(concept.split()) > 8:
            return
        key = (concept.lower(), definition.lower())
        if key in seen:
            return
        seen.add(key)
        pairs.append((concept, definition))

    lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]
    for line in lines:
        if len(pairs) >= max_pairs:
            break
        s = re.sub(r"\s+", " ", line)
        if len(s) < 26 or len(s) > 320:
            continue
        # tr: ingilizce "X is/means ..." kalıbı / en: english "X is/means ..." pattern
        m = re.match(
            r"^([A-Za-zÇĞİÖŞÜçğıöşüıİ0-9][A-Za-zÇĞİÖŞÜçğıöşüıİ0-9 \-_/()]{2,65})\s+"
            r"(is|are|means|refers to|describes)\s+(.+)$",
            s,
            flags=re.I,
        )
        if m:
            add_pair(m.group(1).strip(), m.group(3).strip())
            continue
        # tr: türkçe "X şudur/dır ..." kalıbı / en: turkish "X şudur/dır ..." pattern
        m = re.match(
            r"^([A-Za-zÇĞİÖŞÜçğıöşüıİ0-9][A-Za-zÇĞİÖŞÜçğıöşüıİ0-9 \-_/()]{2,65})\s+"
            r"(şudur|dır|dir|dur|tır|tir|tur|demektir|anlamına gelir|tanımlanır|ifade eder|gösterir)\s+(.+)$",
            s,
            flags=re.I,
        )
        if m:
            add_pair(m.group(1).strip(), m.group(3).strip())
            continue
        # tr: "kavram: tanım" veya "kavram - tanım" / en: "concept: gloss" or "concept - gloss"
        m = re.match(
            r"^([A-Za-zÇĞİÖŞÜçğıöşüıİ0-9][A-Za-zÇĞİÖŞÜçğıöşüıİ0-9 \-_/()]{2,65})\s*[:\-–]\s+(.+)$",
            s,
        )
        if m:
            add_pair(m.group(1).strip(), m.group(2).strip())
            continue

    if len(pairs) < min(max_pairs, 10):
        for sent in split_into_sentences(text):
            if len(pairs) >= max_pairs:
                break
            sent = sent.strip()
            if len(sent) < 40 or len(sent) > 300:
                continue
            words = sent.split()
            if len(words) < 5:
                continue
            head = " ".join(words[: min(5, len(words) - 1)])
            if len(head) < 10:
                continue
            add_pair(head, sent)
            if len(pairs) >= max_pairs:
                break

    return pairs[:max_pairs]


# fn: bootstrap_pairs_from_sentences | tr: cümlelerden yedek kavram çiftleri üret / en: bootstrap concept pairs from sentences as fallback
def bootstrap_pairs_from_sentences(text: str, *, min_want: int = 12) -> List[tuple[str, str]]:
    out: List[tuple[str, str]] = []
    seen_def: Set[str] = set()
    raw = re.sub(r"\s+", " ", (text or "").strip())
    raw = re.sub(r";([^\s;،])", r"; \1", raw)
    raw = re.sub(r",([^\s,؛])", r", \1", raw)
    if len(raw) < 80:
        return out
    chunks: List[str] = []
    for part in re.split(r"(?<=[.!?…])\s+", raw):
        p = part.strip()
        if len(p) >= 40:
            chunks.append(p)
    if len(chunks) < 2:
        for part in re.split(r"[;؛]\s*", raw):
            p = part.strip()
            if len(p) >= 40:
                chunks.append(p)
    for sent in chunks:
        sent = re.sub(r"\s+", " ", sent).strip()
        if len(sent) < 40 or len(sent) > 420:
            continue
        defn = shorten_definition(sent, 200)
        if len(defn) < 22:
            continue
        dk = defn.lower()
        if dk in seen_def:
            continue
        seen_def.add(dk)
        words = [w for w in sent.split() if w]
        if len(words) < 7:
            continue
        head = ""
        for take in (5, 4, 6):
            if len(words) <= take:
                continue
            h = " ".join(words[:take])
            if 10 <= len(h) <= 72:
                head = h
                break
        if not head:
            continue
        out.append((head, defn))
        if len(out) >= min_want:
            return out
    return out


# fn: _overlap_weighted_distractors | tr: yanlış şık adaylarını kelime örtüşmesine göre seç / en: pick wrong-option candidates by word overlap
def _overlap_weighted_distractors(
    concept: str,
    pairs: List[tuple[str, str]],
    exclude_definition: str,
    rng: random.Random,
    take: int,
) -> List[str]:
    cw = {w for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ0-9]+", concept.lower()) if len(w) > 2}
    scored: List[tuple[int, str]] = []
    for c, d in pairs:
        if d == exclude_definition or c.lower() == concept.lower():
            continue
        ow = {w for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ0-9]+", c.lower()) if len(w) > 2}
        scored.append((len(cw & ow), d))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    out: List[str] = []
    seen: set[str] = set()
    for _sc, d in scored:
        k = d.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
        if len(out) >= take + 8:
            break
    rng.shuffle(out)
    return out[:take]


# fn: build_rule_based_mcq | tr: kavram çiftlerinden çoktan seçmeli sorular oluştur / en: build multiple-choice questions from concept pairs
def build_rule_based_mcq(
    pairs: List[tuple[str, str]],
    num_questions: int,
    *,
    focus_topics: Optional[Sequence[str]] = None,
    session_difficulty: str = "normal",
    locale: str = "en",
) -> List[QuizQuestion]:
    if not pairs:
        return []
    rng = random.Random(42)
    topics = list(pairs)
    if focus_topics:
        ft = [str(t).strip().lower() for t in focus_topics if str(t).strip()]
        boosted = [p for p in topics if any(f in p[0].lower() or p[0].lower() in f for f in ft)]
        if boosted:
            rest = [p for p in topics if p not in boosted]
            topics = boosted + rest
    use_tr = (locale or "en").strip().lower().startswith("tr")
    # tr: soru türüne göre kök şablonları (tr/en) / en: stem templates by question type (tr/en)
    stem_templates = {
        "definition": (
            (
                "Aşağıdakilerden hangisi «{c}» kavramını en doğru biçimde tanımlar?",
                "«{c}» kavramının akademik olarak en uygun tanımı hangisidir?",
            )
            if use_tr
            else (
                "Which option defines {c} most accurately?",
                "Which definition best reflects {c} in an academic context?",
            )
        ),
        "concept": (
            (
                "«{c}» kavramının temel mantığını en iyi hangi ifade açıklar?",
                "«{c}» için kavramsal olarak en isabetli ifade hangisidir?",
            )
            if use_tr
            else (
                "Which statement best captures the core principle of {c}?",
                "Which statement is conceptually most accurate about {c}?",
            )
        ),
        "comparison": (
            (
                "«{c}» ile «{p}» karşılaştırıldığında en doğru değerlendirme hangisidir?",
                "«{c}» ve «{p}» için en doğru karşılaştırma ifadesi hangisidir?",
            )
            if use_tr
            else (
                "Which statement offers the most accurate comparison between {c} and {p}?",
                "Which comparison between {c} and {p} is the most valid?",
            )
        ),
        "application": (
            (
                "Verilen kısa senaryoda «{c}» yaklaşımının en doğru uygulaması hangisidir?",
                "Bir problem durumunda «{c}» kullanımını en doğru açıklayan seçenek hangisidir?",
            )
            if use_tr
            else (
                "In the given short scenario, which option applies {c} most accurately?",
                "In a problem context, which option best explains the use of {c}?",
            )
        ),
        "interpretation": (
            (
                "«{c}» hakkında aşağıdaki yorumlardan hangisi metinle en tutarlıdır?",
                "«{c}» bağlamında en isabetli çıkarım hangi seçenektedir?",
            )
            if use_tr
            else (
                "Which interpretation about {c} is most consistent with the source?",
                "Which inference about {c} is the most text-faithful?",
            )
        ),
        "formula": (
            (
                "«{c}» için nicel/işlemsel ilişkiyi en doğru veren ifade hangisidir?",
                "«{c}» ile ilgili hesaplama mantığını en doğru özetleyen seçenek hangisidir?",
            )
            if use_tr
            else (
                "Which option best expresses the quantitative/operational relation for {c}?",
                "Which statement most accurately summarizes the calculation logic for {c}?",
            )
        ),
    }

    def _clean_sentence(text: str) -> str:
        s = re.sub(r"\s+", " ", (text or "").strip())
        s = shorten_definition(s, 130)
        if not s:
            return s
        if s[-1] not in ".!?":
            s += "."
        return s

    def _lower_first(text: str) -> str:
        if not text:
            return text
        return text[0].lower() + text[1:] if len(text) > 1 else text.lower()

    def _professionalize_option(text: str) -> str:
        s = _clean_sentence(text)
        if not s:
            return s
        if use_tr:
            s = re.sub(r"^için\s+", "", s, flags=re.I)
            s = re.sub(r"^doğru ifade[:\s-]*", "", s, flags=re.I)
        else:
            s = re.sub(r"^for\s+[^,]{1,40},\s*the\s+correct\s+statement\s+is[:\s-]*", "", s, flags=re.I)
            s = re.sub(r"^the\s+correct\s+statement\s+is[:\s-]*", "", s, flags=re.I)
        return _clean_sentence(s)

    def _option_for_type(qt: str, label: str, defn: str) -> str:
        base = _professionalize_option(defn)
        if qt == "application":
            k = int(hashlib.sha256(f"{label}|{defn}|app".encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
            if use_tr:
                variants = (
                    f"«{label}» yaklaşımı şu durumda en uygundur: {_lower_first(base)}",
                    f"{_lower_first(base)} Bu nedenle «{label}» uygulanmalıdır",
                    f"Bu tür bir problemde «{label}» tercih edilir; çünkü {_lower_first(base)}",
                )
                return _clean_sentence(variants[k % len(variants)])
            variants = (
                f"{label} is most suitable when {_lower_first(base)}",
                f"{_lower_first(base)} Therefore, {label} is the best choice",
                f"In this type of problem, {label} is preferred because {_lower_first(base)}",
            )
            return _clean_sentence(variants[k % len(variants)])
        if qt == "comparison":
            k = int(hashlib.sha256(f"{label}|{defn}|cmp".encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
            if use_tr:
                variants = (
                    f"«{label}» için temel değerlendirme şöyledir: {_lower_first(base)}",
                    f"{_lower_first(base)} Bu ifade «{label}» ile tutarlıdır",
                    f"«{label}» bağlamında doğru yorum şudur: {_lower_first(base)}",
                )
                return _clean_sentence(variants[k % len(variants)])
            variants = (
                f"The key statement for {label} is that {_lower_first(base)}",
                f"{_lower_first(base)} This is consistent with {label}",
                f"For {label}, the accurate interpretation is that {_lower_first(base)}",
            )
            return _clean_sentence(variants[k % len(variants)])
        return base

    questions: List[QuizQuestion] = []
    nt = len(topics)
    diff = normalize_session_difficulty(session_difficulty)
    base_cycle: Tuple[str, ...] = tuple(TOPIC_QUESTION_TYPES)
    if diff == "technical":
        qtypes = base_cycle + ("interpretation", "formula")
    elif diff == "normal":
        qtypes = base_cycle + ("interpretation",)
    else:
        qtypes = base_cycle
    for i in range(max(0, int(num_questions))):
        concept, definition = topics[i % nt]
        qt = qtypes[i % len(qtypes)]
        pool_rel = _overlap_weighted_distractors(concept, pairs, definition, rng, 18)
        fallback_pairs = [(c, d) for c, d in pairs if c.lower() != concept.lower() and d != definition]
        rng.shuffle(fallback_pairs)
        distractor_pairs: List[tuple[str, str]] = []
        seen_defs: set[str] = set()
        for d in pool_rel:
            dk = d.strip().lower()
            if not dk:
                continue
            c2 = next((c for c, dv in fallback_pairs if dv == d), "")
            distractor_pairs.append((c2 or "Related concept", d))
            seen_defs.add(dk)
            if len(distractor_pairs) >= 3:
                break
        if len(distractor_pairs) < 3:
            for c2, d2 in fallback_pairs:
                dk = d2.strip().lower()
                if not dk or dk in seen_defs:
                    continue
                distractor_pairs.append((c2, d2))
                seen_defs.add(dk)
                if len(distractor_pairs) >= 3:
                    break
        if len(distractor_pairs) < 3:
            continue

        correct_opt = _option_for_type(qt, concept, definition)
        wrong_opts = [_option_for_type(qt, c2, d2) for c2, d2 in distractor_pairs[:3]]
        options = normalize_quiz_options([correct_opt] + wrong_opts)[:4]
        if len(options) < 4:
            continue
        rng.shuffle(options)
        ca = correct_opt
        if ca not in options:
            alt = next((o for o in options if o.lower() == ca.lower()), None)
            ca = alt if alt else options[0]
        peer_concept = topics[(i + 1) % nt][0] if nt > 1 else concept
        stems_for_type = stem_templates.get(qt) or stem_templates["concept"]
        c_stem = shorten_definition(concept, 56).strip().rstrip(".")
        p_stem = shorten_definition(peer_concept, 56).strip().rstrip(".")
        stem = stems_for_type[i % len(stems_for_type)].format(c=c_stem or concept, p=p_stem or peer_concept)
        stem = clip_quiz_visible_text(stem, 280)
        questions.append(
            QuizQuestion(
                question_text=stem,
                options=options[:4],
                correct_answer=ca,
                topic=clip_quiz_visible_text(concept, 120),
                explanation=clip_quiz_visible_text(f"{concept}: {definition}", 280),
                source_section=None,
                question_type=qt,
                difficulty=normalize_session_difficulty(session_difficulty),
            )
        )
    return questions
