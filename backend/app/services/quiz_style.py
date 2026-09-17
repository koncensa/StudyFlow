# svc: quiz_style | tr: quiz soru metnini normalize et ve yazım cilası uygula / en: normalize quiz question wording and apply style polish

from __future__ import annotations

import re
import unicodedata
from typing import List

from app.schemas.quiz_schema import QuizQuestion

from app.services.quiz_validation import (
    clip_quiz_visible_text,
    option_len_max,
    question_len_max,
    strip_leading_enumeration_prefix,
)

# tr: özel tırnak/boşluk karakterlerini düz ascii'ye çevir / en: map fancy quotes/spaces to plain ascii
_QUIZ_QUOTE_TRANSLATE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2039": "'",
        "\u203a": "'",
        "\u00a0": " ",  # nbsp
        "\u202f": " ",  # narrow nbsp
        "\u2009": " ",  # thin space
        "\u200a": "",  # hair space → drop
        "\u2007": " ",  # figure space
    }
)

# tr: bozuk yardımcı fiil birleşmelerini düzelt (en) / en: fix broken auxiliary verb glues (english)
_AUX_GLUE_FIXES = (
    (r"\bhas\s+is\b", "has"),
    (r"\bhave\s+is\b", "have"),
    (r"\bhad\s+is\b", "had"),
    (r"\bare\s+is\b", "are"),
    (r"\bis\s+are\b", "is"),
    (r"\bwas\s+were\b", "was"),
    (r"\bwere\s+was\b", "were"),
    (r"\bwill\s+would\b", "will"),
    (r"\bwould\s+will\b", "would"),
)

# tr: satır sonunda yarim kalan ingilizce kelimeler / en: trailing dangling english words
_TRAILING_EN_DANGLING_WORDS = (
    "and",
    "or",
    "because",
    "if",
    "that",
    "which",
    "with",
    "for",
    "of",
    "to",
    "from",
    "into",
    "about",
    "than",
)

# tr: satır sonunda yarim kalan türkçe kelimeler / en: trailing dangling turkish words
_TRAILING_TR_DANGLING_WORDS = (
    "ve",
    "veya",
    "çünkü",
    "için",
    "eğer",
    "ile",
    "gibi",
    "kadar",
    "göre",
    "üzerine",
)

# tr: birleşik kelime onarımı için ingilizce ekler / en: english suffixes for split-word repair
_EN_SPLIT_SUFFIXES = (
    "tion",
    "sion",
    "ment",
    "ness",
    "ship",
    "ality",
    "alitys",
    "al",
    "als",
    "ing",
    "ed",
    "er",
    "ers",
    "ly",
    "ive",
    "ity",
    "ies",
    "ent",
    "ents",
    "ance",
    "ence",
    "ary",
    "ory",
    "able",
    "ible",
    "ful",
    "less",
    "ism",
    "ist",
    "ists",
)


# fn: _sanitize_quiz_unicode | tr: emoji/görünmez karakterleri temizle, unicode normalize et / en: strip emoji/invisible chars, normalize unicode
def _sanitize_quiz_unicode(s: str) -> str:
    if not s:
        return s
    t = unicodedata.normalize("NFC", s)
    t = t.translate(_QUIZ_QUOTE_TRANSLATE)
    out: List[str] = []
    for ch in t:
        o = ord(ch)
        if o == 0xFEFF:
            continue
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        if cat in ("Zl", "Zp"):
            out.append(" ")
            continue
        if cat == "Zs" and ch != " ":
            out.append(" ")
            continue
        if cat == "Cc":
            if ch in "\t\n\r":
                out.append(" ")
            continue
        if 0xE000 <= o <= 0xF8FF:
            continue
        if 0xFFF0 <= o <= 0xFFFF:
            continue
        if 0xFE00 <= o <= 0xFE0F:
            continue
        if 0x1F300 <= o <= 0x1FAFF:
            continue
        if 0x2600 <= o <= 0x26FF or 0x2700 <= o <= 0x27BF:
            continue
        out.append(ch)
    return "".join(out)


# fn: _dedupe_adjacent_words | tr: yan yana tekrarlanan kelimeleri birleştir / en: dedupe adjacent repeated words
def _dedupe_adjacent_words(s: str) -> str:
    out = (s or "").strip()
    if not out:
        return out
    for _ in range(4):
        nxt = re.sub(r"\b([A-Za-zÇĞİÖŞÜçğıöşüİı0-9]{2,})\s+\1\b", r"\1", out, flags=re.I)
        if nxt == out:
            break
        out = nxt
    return out


# fn: _trim_dangling_tail_words | tr: satır sonundaki yarim bağlaç/kelimeyi kes / en: trim dangling conjunctions at line end
def _trim_dangling_tail_words(s: str, *, use_tr: bool) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    if not t:
        return t
    dangling = _TRAILING_TR_DANGLING_WORDS if use_tr else _TRAILING_EN_DANGLING_WORDS
    while True:
        low = t.casefold()
        hit = False
        for w in dangling:
            if re.search(rf"\b{re.escape(w)}\s*$", low):
                t = re.sub(r"\s+\S+\s*$", "", t).strip()
                hit = True
                break
        if not hit:
            return t
        if len(t) < 8:
            return ""


# fn: _strip_leading_filler_phrases | tr: şık/açıklama başındaki boş giriş cümlelerini sil / en: strip filler openers from options/explanations
def _strip_leading_filler_phrases(s: str, *, use_tr: bool) -> str:
    t = (s or "").strip()
    if not t:
        return t
    en_patterns = (
        r"^according to (?:the )?(?:text|passage|material|source|document|pdf),?\s+",
        r"^based on (?:the )?(?:text|passage|material|source|document|pdf),?\s+",
        r"^as (?:stated|mentioned|noted|described) (?:in )?(?:the )?(?:text|passage|material|source|document|above|earlier|previously),?\s+",
        r"^from (?:the )?(?:text|passage|material|source|document),?\s+",
        r"^it (?:is |was )?(?:important |worth )?to note that\s+",
        r"^it should be noted that\s+",
        r"^please note that\s+",
        r"^note that\s+",
        r"^in this (?:case|context|scenario|situation),?\s+",
        r"^generally speaking,?\s+",
        r"^(?:actually|basically|essentially|clearly|obviously),?\s+",
    )
    tr_patterns = (
        r"^metne göre,?\s+",
        r"^yukarıdaki metne göre,?\s+",
        r"^metinde(?: belirtildiği üzere)?,?\s+",
        r"^belirtmek gerekir ki[,:\s]+",
        r"^şunu belirtmek gerekir ki[,:\s]+",
        r"^bu bağlamda,?\s+",
        r"^bu senaryoda,?\s+",
        r"^öncelikle,?\s+",
        r"^açıkçası,?\s+",
    )
    for _ in range(5):
        prev = t
        for pat in (tr_patterns if use_tr else en_patterns):
            t = re.sub(pat, "", t, flags=re.IGNORECASE | re.UNICODE)
        if use_tr:
            for pat in en_patterns:
                t = re.sub(pat, "", t, flags=re.IGNORECASE | re.UNICODE)
        t = re.sub(r"^\s+[,;:\-–—]+\s*", "", t).strip()
        if t == prev:
            break
    return t


# fn: _strip_trailing_enumeration_garbage | tr: satır sonundaki liste numarası artıklarını sil / en: strip trailing list-number garbage
def _strip_trailing_enumeration_garbage(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    if not t:
        return t
    for _ in range(4):
        prev = t
        t = re.sub(r"[,;:]\s*\d{1,2}[.)]?\s*$", "", t).strip()
        t = re.sub(r"\s+\d{1,2}\.\s*$", "", t).strip()
        t = re.sub(r"\s+\d{1,2}\)\s*$", "", t).strip()
        if t == prev:
            break
    return t


# fn: _heal_split_word_artifacts | tr: ocr/llm kelime bölünmesini onar (profession als) / en: heal split-word ocr/llm artifacts
def _heal_split_word_artifacts(s: str, *, use_tr: bool) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    if not t:
        return t
    out = t
    for _ in range(3):
        prev = out
        suffix_group = "|".join(sorted(_EN_SPLIT_SUFFIXES, key=len, reverse=True))
        out = re.sub(
            rf"\b([A-Za-z]{{3,}})\s+({suffix_group})\b",
            lambda m: f"{m.group(1)}{m.group(2)}",
            out,
            flags=re.I,
        )
        out = re.sub(r"\b([A-Za-z]{2,4})\s+(ents?)\b", r"\1\2", out, flags=re.I)
        if out == prev:
            break
    for _ in range(2):
        prev = out
        out = re.sub(r"\b([A-Za-z]{7,}ati)\s+on\b", r"\1on", out, flags=re.I)
        out = re.sub(r"\b([A-Za-z]{6,}iti)\s+on\b", r"\1on", out, flags=re.I)
        out = re.sub(r"\b([A-Za-z]{5,}cti)\s+on\b", r"\1on", out, flags=re.I)
        if out == prev:
            break
    return out


# fn: _heal_glued_english_conjunctions | tr: yapışık and/or arasına boşluk ekle / en: insert spaces in glued and/or conjunctions
def _heal_glued_english_conjunctions(s: str, *, use_tr: bool) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    if not t or use_tr:
        return t
    out = t
    for _ in range(2):
        prev = out
        out = re.sub(r"\b([A-Za-z]{4,})(or)([A-Za-z]{4,})\b", r"\1 or \3", out)
        out = re.sub(r"\b([A-Za-z]{4,})(and)([A-Za-z]{4,})\b", r"\1 and \3", out)
        if out == prev:
            break
    return out


# fn: _drop_unbalanced_parentheses | tr: dengesiz parantezleri temizle / en: remove unbalanced parentheses
def _drop_unbalanced_parentheses(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return t
    chars: List[str] = []
    open_pos: List[int] = []
    for ch in t:
        if ch == "(":
            open_pos.append(len(chars))
            chars.append(ch)
            continue
        if ch == ")":
            if not open_pos:
                continue
            open_pos.pop()
            chars.append(ch)
            continue
        chars.append(ch)
    if open_pos:
        drop = set(open_pos)
        chars = [c for i, c in enumerate(chars) if i not in drop]
    return "".join(chars)


# fn: _strip_quiz_artifact_prefix | tr: baştaki liste/numara artıklarını sil (1), 12., •) / en: strip leading list/number artifacts
def _strip_quiz_artifact_prefix(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return t
    for _ in range(6):
        nxt = t
        nxt = re.sub(r"^\s*\d{1,3}\s*\)\s+", "", nxt)
        nxt = re.sub(r"^\s*\d{1,3}\)(?=[A-Za-zÇĞİÖŞÜçğıöşüıİ\"'«(])", "", nxt)
        nxt = re.sub(r"^\s*\(\s*\d{1,3}\s*\)\s*", "", nxt)
        if not re.match(r"^\d+\.\d", nxt.strip()):
            nxt = re.sub(r"^\s*\d{1,3}\.\s+", "", nxt)
        nxt = re.sub(r"^\s*\d{1,3}\s*:\s+", "", nxt)
        nxt = re.sub(r"^\s*[•●▪◦·‣⁃]\s*", "", nxt)
        nxt = re.sub(r"^\s*[-–—]\s+", "", nxt)
        nxt = re.sub(r"^\s*\.\s+", "", nxt)
        nxt = re.sub(r"^\s*\.\s*veya\s+", "", nxt, flags=re.I)
        nxt = re.sub(r"^\s*veya\s+", "", nxt, flags=re.I)
        nxt = nxt.strip()
        if nxt == t:
            break
        t = nxt
    return t


# fn: _strip_quiz_inline_garbage | tr: satır içi madde işareti/numara artıklarını sil / en: strip inline bullet/number garbage
def _strip_quiz_inline_garbage(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return t
    t = re.sub(r"\s*[•●▪◦·‣⁃]\s*", " ", t)
    t = re.sub(r"\s+[1-9]\d?\)\s+", " ", t)
    t = re.sub(
        r"\s+([1-9]\d?)\)([A-Za-zÇĞİÖŞÜçğıöşüıİ\"'«(])",
        r" \2",
        t,
    )
    return re.sub(r"\s{2,}", " ", t).strip()


# fn: sanitize_quiz_topic_label | tr: gürültülü konu başlığını kısa ui etiketine çevir / en: turn noisy topic heading into short ui label
def sanitize_quiz_topic_label(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    if not t:
        return t
    t = _sanitize_quiz_unicode(t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return t
    t = _strip_quiz_artifact_prefix(t)
    t = _strip_quiz_inline_garbage(t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return t
    t = re.sub(r"^(?:Part\s+[AB])\s*[-–—]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+this\s+notebook\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+this\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+we\s+create\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+we\s+create\??\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+will\s+try\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+(will|must|can)\s+(try|find)\s*$", "", t, flags=re.I)
    t = re.sub(r"^(?:Part\s+[A-D])\s*[-–—]\s*", "", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—:")
    if len(t) > 14 and t.upper() == t and re.search(r"[A-Z]", t):
        t = t.title()
    parts = t.split()
    if len(parts) > 10:
        t = " ".join(parts[:10])
    return t[:120]


# fn: normalize_quiz_text_style | tr: soru/şık metnini locale'e göre normalize et / en: normalize question/option text by locale
def normalize_quiz_text_style(
    text: str,
    *,
    locale: str,
    is_question: bool,
    max_chars: int,
) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return s
    s = _sanitize_quiz_unicode(s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return s
    s = _strip_quiz_artifact_prefix(s)
    s = _strip_quiz_inline_garbage(s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return s
    use_tr = (locale or "en").strip().lower().startswith("tr")
    if not is_question:
        s = _strip_leading_filler_phrases(s, use_tr=use_tr)
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            return ""
    if not use_tr:
        for pat, rep in (
            (r"\be xactly\b", "exactly"),
            (r"\bE xactly\b", "Exactly"),
            (r"\bgene rali[sz]e\b", "generalise"),
            (r"\bgene ralise\b", "generalise"),
            (r"\bi\s+nstead\b", "instead"),
            (r"\bu\s+nless\b", "unless"),
            (r"\bu\s+nder\b", "under"),
            (r"\bo\s+therwise\b", "otherwise"),
        ):
            s = re.sub(pat, rep, s, flags=re.I)
    else:
        for pat, rep in (
            (r"\bB\s+ir\b", "Bir"),
            (r"\bb\s+ir\b", "bir"),
            (r"\bV\s+e\b", "Ve"),
            (r"\bv\s+e\b", "ve"),
            (r"\bİ\s+çin\b", "İçin"),
            (r"\bi\s+çin\b", "için"),
            (r"\bİ\s+le\b", "İle"),
            (r"\bi\s+le\b", "ile"),
            (r"\bD\s+e\s+ğil\b", "Değil"),
            (r"\bd\s+e\s+ğil\b", "değil"),
            (r"\bD\s+eğil\b", "Değil"),
            (r"\bd\s+eğil\b", "değil"),
            (r"\bO\s+lan\b", "Olan"),
            (r"\bo\s+lan\b", "olan"),
            (r"\bM\s+i\s+dir\b", "Midir"),
            (r"\bm\s+i\s+dir\b", "midir"),
            (r"\bY\s+oktur\b", "Yoktur"),
            (r"\by\s+oktur\b", "yoktur"),
        ):
            s = re.sub(pat, rep, s)

    def _replace_quoted_list(match: re.Match[str]) -> str:
        raw = match.group(0)
        parts = [a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", raw)]
        cleaned: List[str] = []
        seen: set[str] = set()
        for p in parts:
            item = re.sub(r"\s+", " ", p.strip())
            if not item:
                continue
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(item)
        if not cleaned:
            return raw
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} {'ve' if use_tr else 'and'} {cleaned[1]}"
        glue = " ve " if use_tr else " and "
        return ", ".join(cleaned[:-1]) + glue + cleaned[-1]

    s = s.replace("`", "")
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = re.sub(r"\[(?:\s*['\"][^'\"]+['\"]\s*,?){2,}\s*\]", _replace_quoted_list, s)
    s = re.sub(r"(?:(?<=\s)|^)'([A-Za-z0-9_ /-]{2,40})'(?=(?:\s|[,.);:]|$))", r"\1", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([,.;:!?]){2,}", r"\1", s)
    s = _dedupe_adjacent_words(s)
    for pat, rep in _AUX_GLUE_FIXES:
        s = re.sub(pat, rep, s, flags=re.I)
    s = re.sub(r"\b(mi|mı|mu|mü)\s+\1\b", r"\1", s, flags=re.I)
    s = re.sub(r"([$\u20BA\u20AC\u00A3])\s+([0-9])", r"\1\2", s)
    s = re.sub(r"^[A-Da-d][\).:\-]\s*", "", s)
    s = re.sub(r"^[•●▪◦·]\s*", "", s)
    s = re.sub(r"^[\.\-:]\s+", "", s)
    s = re.sub(
        r"^for\s+[^,]{1,100},\s*the\s+correct\s+statement\s+is[:\s-]*",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"^the\s+correct\s+statement\s+is[:\s-]*", "", s, flags=re.I)
    s = re.sub(r"^a\s+valid\s+statement\s+about\s+[^,]{1,120}\s+is\s+that\s+", "", s, flags=re.I)
    s = re.sub(r"^in\s+this\s+scenario,\s*[^,]{1,120}\s+is\s+appropriate\s+because\s+", "", s, flags=re.I)
    s = re.sub(r"^in\s+this\s+scenario,\s*", "", s, flags=re.I)
    s = re.sub(r"^[«\"]?[^»\"]{2,100}[»\"]?\s+için\s+doğru\s+ifade\s*[:\-]\s*", "", s, flags=re.I)
    s = re.sub(r"^doğru\s+ifade\s*[:\-]\s*", "", s, flags=re.I)
    s = re.sub(r"^bu\s+senaryoda,\s*[^,]{1,120}\s+(yaklaşımı\s+)?uygundur\s+çünkü\s+", "", s, flags=re.I)
    s = re.sub(r"^bu\s+senaryoda,\s*", "", s, flags=re.I)
    s = re.sub(r"\(\s*([^)]+?)\s*\)", r"(\1)", s)
    s = s.strip(" -:;")
    s = _strip_trailing_enumeration_garbage(s)
    s = _heal_split_word_artifacts(s, use_tr=use_tr)
    s = _heal_glued_english_conjunctions(s, use_tr=use_tr)
    s = _drop_unbalanced_parentheses(s)
    s = s.strip(" -:;,")
    if s and s[0].isalpha():
        s = s[0].upper() + s[1:]
    if len(s) > max_chars:
        s = clip_quiz_visible_text(s, max_chars)
    s = _trim_dangling_tail_words(s, use_tr=use_tr)
    if not s:
        return ""
    if is_question:
        s = re.sub(r"\s+will\s+try\s*\??\s*$", "", s, flags=re.I)
        s = re.sub(r"\s+(will|must)\s+find\s*\??\s*$", "", s, flags=re.I)
        q_starts = (
            "which",
            "what",
            "how",
            "why",
            "when",
            "where",
            "who",
            "hangi",
            "hangisi",
            "aşağıdakilerden",
            "verilen",
            "aşağıdaki",
        )
        if s and s[-1] not in ".!?":
            low = s.lower()
            s = s + ("?" if low.startswith(q_starts) else ".")
        elif s.endswith("."):
            low = s.lower()
            if low.startswith(q_starts):
                s = s[:-1] + "?"
    else:
        if s:
            s = s.rstrip()
    return s


# fn: polish_quiz_question_style | tr: tek soruya tam stil cilası uygula (kök, şık, konu) / en: apply full style polish to one question
def polish_quiz_question_style(q: QuizQuestion, locale: str) -> QuizQuestion:
    opts_old = list(q.options or [])
    if len(opts_old) != 4:
        return q
    corr = (q.correct_answer or "").strip()
    idx = -1
    for i, o in enumerate(opts_old):
        if o == corr:
            idx = i
            break
    if idx < 0:
        cfl = corr.casefold()
        for i, o in enumerate(opts_old):
            if str(o).strip().casefold() == cfl:
                idx = i
                break
    stem = normalize_quiz_text_style(
        q.question_text or "",
        locale=locale,
        is_question=True,
        max_chars=question_len_max(),
    )
    opts_new = [
        normalize_quiz_text_style(
            strip_leading_enumeration_prefix(str(o)),
            locale=locale,
            is_question=False,
            max_chars=option_len_max(),
        )
        for o in opts_old
    ]
    if len({x.casefold() for x in opts_new}) < 4:
        opts_new = opts_old
    corr_new = opts_new[idx] if 0 <= idx < len(opts_new) else corr
    exp_raw = (q.explanation or "").strip()
    exp_cap = min(2400, max(400, option_len_max() * 2))
    exp_new = (
        normalize_quiz_text_style(exp_raw, locale=locale, is_question=False, max_chars=exp_cap)
        if exp_raw
        else None
    )
    topic_clean = sanitize_quiz_topic_label(q.topic or "")
    return q.model_copy(
        update={
            "question_text": stem or q.question_text,
            "options": opts_new,
            "correct_answer": corr_new,
            "explanation": exp_new,
            "topic": topic_clean or q.topic,
        }
    )
