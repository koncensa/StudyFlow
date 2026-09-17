# svc: suggestion_service | tr: quiz sonucuna göre kişiselleştirilmiş çalışma önerileri üret / en: build personalized study suggestions from quiz results

import os
from typing import List, Optional, Sequence, Tuple

from app.schemas.topic_schema import TopicPerformance
from app.schemas.quiz_schema import QuizSubmissionResponse
from app.schemas.suggestion_schema import (
    AdaptiveStudyPlan,
    SuggestionAction,
    SuggestionResponse,
    TopicSuggestion,
)
from app.services.study_coaching_service import build_coaching_pack


# fn: _use_ollama_coach | tr: ollama koç mesajı kullanılsın mı (env) / en: whether to use ollama for coach message
def _use_ollama_coach() -> bool:
    v = os.getenv("OLLAMA_COACH", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return os.getenv("OLLAMA_ENABLED", "true").lower() in ("1", "true", "yes")


# fn: _is_tr | tr: çıktı dili türkçe mi / en: whether output locale is turkish
def _is_tr(locale: str) -> bool:
    return (locale or "en").strip().lower().startswith("tr")


# fn: _remedial_topic_names | tr: telafi edilmesi gereken zayıf+çok zayıf konu listesi / en: very weak and weak topics needing catch-up
def _remedial_topic_names(ta) -> List[str]:
    """Topics that need catch-up first: very weak + weak bands."""
    out: List[str] = []
    for t in getattr(ta, "very_weak_topics", None) or []:
        if t and t not in out:
            out.append(t)
    for t in ta.weak_topics or []:
        if t and t not in out:
            out.append(t)
    return out


# fn: _rule_based_coach_message | tr: skor ve konu bantlarına göre kural tabanlı koç mesajı / en: rule-based coach message from score and topic bands
def _rule_based_coach_message(result: QuizSubmissionResponse, *, output_locale: str = "en") -> str:
    tr = _is_tr(output_locale)
    score = result.score_percentage
    ta = result.topic_analysis
    remedial = _remedial_topic_names(ta)
    dev = list(ta.developing_topics or ta.moderate_topics or [])

    parts: List[str] = []
    if tr:
        if score >= 75 and not remedial:
            parts.append(f"Genel skorun %{score:.0f} — iyi gidiyorsun. Güçlü konuları aralıklı tekrar et.")
        elif score >= 60:
            parts.append(
                f"Genel %{score:.0f}. "
                + (
                    f"Önce {len(remedial)} zayıf konuya odaklan; en hızlı kazanım oradan gelir."
                    if remedial
                    else "Gelişmekte olan konulara kısa tekrar ekle."
                )
            )
        else:
            parts.append(
                f"Bu denemede %{score:.0f}. Aşağıdaki adımlar tam olarak yanlış yaptığın yerlere göre sıralı."
            )
        if getattr(ta, "very_weak_topics", None):
            vw = ", ".join(ta.very_weak_topics[:4])
            parts.append(f"Çok zayıf (0–40%): {vw}" + (" …" if len(ta.very_weak_topics) > 4 else "") + " — önce bunları toparla.")
        if ta.weak_topics:
            w = ", ".join(ta.weak_topics[:4])
            parts.append(f"Zayıf (40–60%): {w}" + (" …" if len(ta.weak_topics) > 4 else "") + " — PDF’de ilgili bölüm + mini quiz.")
        if not remedial and dev:
            parts.append(f"Geliştir (60–75%): {', '.join(dev[:4])}. Kısa soru setleri genelde yetiyor.")
        if not remedial and not dev:
            parts.append("Bu quizde zayıf bant yok; karışık tekrar ve bir tık daha zor soru iyi gider.")
    else:
        if score >= 75 and not remedial:
            parts.append(
                f"Solid run at {score:.0f}% overall — you are in a good place. Keep spacing reviews so it stays automatic."
            )
        elif score >= 60:
            parts.append(
                f"Overall {score:.0f}%. "
                + (
                    f"Start with your {len(remedial)} weaker topic(s) first — that is the fastest lift."
                    if remedial
                    else "Next, polish the developing topics with short drills."
                )
            )
        else:
            parts.append(
                f"This attempt landed around {score:.0f}%. The steps below follow what you actually missed."
            )
        if getattr(ta, "very_weak_topics", None):
            vw = ", ".join(ta.very_weak_topics[:4])
            parts.append(
                f"Very weak (0–40%): {vw}"
                + (" …" if len(ta.very_weak_topics) > 4 else "")
                + " — rebuild these first."
            )
        if ta.weak_topics:
            w = ", ".join(ta.weak_topics[:4])
            parts.append(
                f"Weak (40–60%): {w}"
                + (" …" if len(ta.weak_topics) > 4 else "")
                + " — re-open the matching PDF chunks, then micro-quiz."
            )
        if not remedial and dev:
            parts.append(
                f"Developing (60–75%): {', '.join(dev[:4])}. A tiny question set usually nudges these up."
            )
        if not remedial and not dev:
            parts.append("No weak band on this quiz; keep momentum with mixed review.")

    return " ".join(parts)


# fn: _maybe_ollama_coach | tr: ollama koç mesajı dene, yoksa kural tabanlıya düş / en: try ollama coach message, fallback to rule-based
def _maybe_ollama_coach(
    result: QuizSubmissionResponse, user_id: int, output_locale: str = "en"
) -> str:
    if not _use_ollama_coach():
        return _rule_based_coach_message(result, output_locale=output_locale)
    try:
        from app.services.ollama_service import coach_message_with_ollama, ollama_available

        if not ollama_available():
            return _rule_based_coach_message(result, output_locale=output_locale)
        topic_summary = [
            {
                "topic": tp.topic,
                "success_rate": round(tp.success_rate * 100, 1),
                "status": tp.status,
                "wrong_count": tp.wrong_count,
                "total": tp.total_attempts,
            }
            for tp in result.topic_analysis.topics
        ]
        out = coach_message_with_ollama(
            result.score_percentage,
            topic_summary,
            user_id,
            output_locale=output_locale,
            confused_topics_ranked=list(result.confused_topics_ranked or []),
            follow_up_actions=list(result.follow_up_actions or []),
        )
        if out and len(out.strip()) > 20:
            return out.strip()
    except Exception:
        pass
    return _rule_based_coach_message(result, output_locale=output_locale)


# fn: _resource_query | tr: konu için dış kaynak arama sorgusu / en: external resource search query for topic
def _resource_query(topic: str) -> str:
    return f"{topic} lecture notes summary practice questions"


# fn: _tier_hint | tr: başarı yüzdesine göre kısa tier ipucu / en: short tier hint by success rate
def _tier_hint(rate_pct: int, tr: bool) -> str:
    if rate_pct < 50:
        return (
            "Bu konuyu baştan toparla; ardından bu konudan mini quiz çöz."
            if tr
            else "Re-learn this topic, then run a mini-quiz on it."
        )
    if rate_pct < 70:
        return "Biraz daha pratik yap — kısa tekrar + birkaç soru." if tr else "A bit more practice — short recap + a few questions."
    return "İyi gidiyorsun; bir uygulama sorusuyla pekiştir." if tr else "You are doing fine here; lock it in with one applied question."


# fn: _actions_for_topic | tr: konu performansına göre önerilen eylem listesi / en: suggested actions for topic performance band
def _actions_for_topic(tp: TopicPerformance, *, tr: bool = False) -> List[SuggestionAction]:
    t = tp.topic
    rate_pct = round(tp.success_rate * 100)
    wrong = tp.wrong_count
    total = tp.total_attempts
    st = tp.status
    if st == "moderate":
        st = "developing"

    actions: List[SuggestionAction] = []
    actions.append(
        SuggestionAction(
            action="review",
            message=_tier_hint(rate_pct, tr),
        )
    )

    if st == "very_weak":
        actions.append(
            SuggestionAction(
                action="review",
                message=(
                    f"«{t}» bu quizde %{rate_pct} — neredeyse tüm sorularda zorlanmış olabilirsin. "
                    f"Önce tanımı ve örneği tek sayfada özetle ({wrong}/{total} yanlış)."
                    if tr
                    else (
                        f"«{t}» is only ~{rate_pct}% here — likely shaky across several items "
                        f"({wrong}/{total} wrong). Rebuild the core idea on one page first."
                    )
                ),
            )
        )
        actions.append(
            SuggestionAction(
                action="mini_quiz",
                message=(
                    f"Bu konudan mini quiz çöz (3 soru); yanlışta hemen PDF'deki ilgili başlığa dön."
                    if tr
                    else f"Run a 3-question micro-quiz on «{t}»; on a miss, jump back to that PDF heading."
                ),
            )
        )
        actions.append(
            SuggestionAction(
                action="pomodoro",
                pomodoro_minutes=35,
                message=(
                    f"«{t}» için 35 dk tek konu, telefon uzakta."
                    if tr
                    else f"35 distraction-free minutes on «{t}» only."
                ),
            )
        )
        return actions

    if st == "weak":
        actions.append(
            SuggestionAction(
                action="review",
                message=(
                    f"«{t}» bu quizde %{rate_pct} ({total - wrong}/{total} doğru). "
                    f"PDF veya özetinde yalnızca «{t}» ile ilgili kısımları yeniden oku; üstünden geçer gibi değil, kendi cümlelerinle özetle."
                    if tr
                    else (
                        f"On «{t}» you landed ~{rate_pct}% ({total - wrong}/{total} correct). "
                        f"Re-read only the «{t}» chunks in your PDF or summary, then paraphrase aloud."
                    )
                ),
            )
        )
        actions.append(
            SuggestionAction(
                action="resource",
                message=(
                    f"İstersen «{t}» için farklı bir anlatım ara (video / not / çözümlü örnek)."
                    if tr
                    else f"If «{t}» still feels fuzzy, try a second explanation (video, notes, worked examples)."
                ),
                resource_query=_resource_query(t),
            )
        )
        actions.append(
            SuggestionAction(
                action="mini_quiz",
                message=(
                    f"Sonuçlar ekranından «{t}» odaklı 3 soruluk mini quiz dene."
                    if tr
                    else f"Use Results to run a 3-question micro-quiz focused on «{t}»."
                ),
            )
        )
        pomodoro = 30 if tp.success_rate < 0.35 else 25
        actions.append(
            SuggestionAction(
                action="pomodoro",
                pomodoro_minutes=pomodoro,
                message=(
                    f"«{t}» için {pomodoro} dakika tek başlık."
                    if tr
                    else f"{pomodoro} minutes on «{t}» only — phone away."
                ),
            )
        )
        return actions

    if st == "developing":
        actions.append(
            SuggestionAction(
                action="review",
                message=(
                    f"«{t}» gelişiyor (%{rate_pct}). Özette hâlâ bulanık gelen tek fikri yaz."
                    if tr
                    else f"«{t}» is developing ({rate_pct}%). Write the one idea that still feels fuzzy."
                ),
            )
        )
        actions.append(
            SuggestionAction(
                action="mini_quiz",
                message=(
                    f"«{t}» için 2–3 hızlı soru; yanlışta aynı alt başlığa dön."
                    if tr
                    else f"2–3 lightning questions on «{t}»; if you miss, reopen that subheading."
                ),
            )
        )
        actions.append(
            SuggestionAction(
                action="resource",
                message=(f"İsteğe bağlı: «{t}» için ek kaynak." if tr else f"Optional extra material for «{t}»."),
                resource_query=_resource_query(t),
            )
        )
        actions.append(
            SuggestionAction(
                action="pomodoro",
                pomodoro_minutes=15,
                message=("15 dk genelde yeter." if tr else "15 focused minutes is often enough here."),
            )
        )
        return actions

    if st == "good":
        actions.append(
            SuggestionAction(
                action="maintenance",
                message=(
                    f"«{t}» iyi (%{rate_pct}). Bir uygulama veya bir tık zor soru ile sabitle."
                    if tr
                    else f"«{t}» is in a good band ({rate_pct}%). Lock it with one applied or harder variant."
                ),
            )
        )
        actions.append(
            SuggestionAction(
                action="mini_quiz",
                message=(
                    f"Karışık tekrarda «{t}» içeren tek soru ekle."
                    if tr
                    else f"Add one mixed-review question that touches «{t}»."
                ),
            )
        )
        return actions

    # tr: strong band / en: strong band
    actions.append(
        SuggestionAction(
            action="maintenance",
            message=(
                f"«{t}» güçlü (%{rate_pct}). Haftada 1–2 karışık soru yeter."
                if tr
                else f"«{t}» is strong ({rate_pct}%). 1–2 mixed questions per week keeps it warm."
            )
        )
    )
    return actions


# fn: _next_step_headline_and_why | tr: bir sonraki adım başlığı ve gerekçesi / en: next step headline and rationale
def _next_step_headline_and_why(result: QuizSubmissionResponse, *, tr: bool) -> Tuple[str, str]:
    ta = result.topic_analysis
    confused = list(result.confused_topics_ranked or [])
    top_miss = confused[0] if confused else None
    remedial = _remedial_topic_names(ta)
    vw = list(getattr(ta, "very_weak_topics", None) or [])

    if vw:
        w0 = vw[0]
        if tr:
            why = f"«{w0}» konusunda başarı oranın düşük (0–40% bandı)."
            if top_miss == w0:
                why += " Bu konuda en çok hata yapmışsın."
            elif top_miss:
                why += f" Ayrıca «{top_miss}» altında da birkaç kayıp var."
        else:
            why = f"You are under ~40% success on «{w0}» for this quiz."
            if top_miss == w0:
                why += " That matches your most-missed topic."
            elif top_miss:
                why += f" You also left several marks on «{top_miss}»."
        return (f"Önce şunu toparla: {w0}" if tr else f"Rebuild: {w0}", why)

    if ta.weak_topics:
        w0 = ta.weak_topics[0]
        if tr:
            why = f"«{w0}» 40–60% bandında; burada netleşme lazım."
            if top_miss == w0:
                why += " En çok takıldığın konu bu."
            elif top_miss:
                why += f" «{top_miss}» da hata listesinde."
        else:
            why = f"«{w0}» sits in the 40–60% band — it needs clarity."
            if top_miss == w0:
                why += " It is also your noisiest topic."
            elif top_miss:
                why += f" «{top_miss}» shows up heavily too."
        return (f"Güçlendir: {w0}" if tr else f"Sharpen: {w0}", why)

    dev = ta.developing_topics or ta.moderate_topics
    if dev:
        d0 = dev[0]
        if tr:
            return (
                f"Parlat: {d0}",
                f"«{d0}» 60–75% aralığında — kısa tekrar + mini quiz genelde yeter.",
            )
        return (
            f"Polish: {d0}",
            f"«{d0}» is 60–75% — a short recap plus a micro-quiz usually bumps it.",
        )

    if ta.good_topics:
        g0 = ta.good_topics[0]
        if tr:
            return (
                f"Pekiştir: {g0}",
                f"«{g0}» iyi gidiyor (75–90%) — bir uygulama sorusuyla otomatikleştir.",
            )
        return (
            f"Consolidate: {g0}",
            f"«{g0}» is solid (75–90%) — one applied question makes it stick.",
        )

    if result.score_percentage < 75 and top_miss:
        if tr:
            return (
                f"Tekrar: {top_miss}",
                "Yanlışların dağılımı burayı gösteriyor — aşağıdaki açıklamaları oku, sonra yeniden dene.",
            )
        return (
            f"Revisit: {top_miss}",
            "Mistake patterns point here — read the wrong-answer notes below, then retry.",
        )

    if tr:
        return ("Karışık tekrar", "Tüm konuları ılık tutmak için kısa bir karışık quiz planla.")
    return ("Mixed review", "Schedule a short mixed quiz to keep every topic warm.")


# fn: _build_adaptive_plan | tr: zayıf/gelişen/güçlü konulara göre uyarlanabilir çalışma planı / en: adaptive study plan from topic bands
def _build_adaptive_plan(result: QuizSubmissionResponse, *, tr: bool) -> AdaptiveStudyPlan:
    ta = result.topic_analysis
    remedial = _remedial_topic_names(ta)
    developing = list(ta.developing_topics or ta.moderate_topics or [])[:8]
    strong = list(ta.strong_topics or [])[:8]
    good_list = list(ta.good_topics or [])[:6]
    resource_queries = [_resource_query(t) for t in remedial[:6]]

    narrative_parts: List[str] = []
    if remedial:
        if tr:
            narrative_parts.append(
                f"Zayıf / çok zayıf konular: {', '.join(remedial[:5])}"
                + (" …" if len(remedial) > 5 else "")
                + ". Neden: bu quizde başarı yüzden düşük. Ne yapmalı: önce PDF'de ilgili bölüm, sonra mini quiz."
            )
        else:
            narrative_parts.append(
                f"Weak spots: {', '.join(remedial[:5])}"
                + (" …" if len(remedial) > 5 else "")
                + ". Why: success rate dipped on this attempt. What to do: reopen those PDF sections, then micro-quiz."
            )
    if developing and not remedial:
        if tr:
            narrative_parts.append(
                f"Gelişmekte: {', '.join(developing[:4])}. Kısa pratikle 75%+ bandına çıkar."
            )
        else:
            narrative_parts.append(
                f"Developing: {', '.join(developing[:4])}. Short drills usually push these past 75%."
            )
    if strong:
        if tr:
            narrative_parts.append(
                f"Güçlü (90%+): {', '.join(strong[:3])}"
                + (" …" if len(strong) > 3 else "")
                + " — haftada bir karışık soru yeter."
            )
        else:
            narrative_parts.append(
                f"Strong (90%+): {', '.join(strong[:3])}"
                + (" …" if len(strong) > 3 else "")
                + " — light mixed review is enough."
            )
    if good_list and not remedial and not developing:
        if tr:
            narrative_parts.append(
                f"İyi band (75–90%): {', '.join(good_list[:3])}. Bir zor varyasyon ekle."
            )
        else:
            narrative_parts.append(
                f"Good band (75–90%): {', '.join(good_list[:3])}. Add one harder variant next time."
            )

    next_title, next_why = _next_step_headline_and_why(result, tr=tr)
    narrative = " ".join(narrative_parts).strip()
    if not narrative:
        narrative = next_why

    all_topic_names = [t.topic for t in ta.topics]
    next_focus = remedial[:6] or developing[:6] or all_topic_names[:5]

    return AdaptiveStudyPlan(
        remedial_topics=remedial[:10],
        challenge_topics=strong,
        resource_queries=resource_queries,
        next_quiz_focus=next_focus,
        narrative=narrative,
        next_step_title=next_title,
        next_step_why=next_why,
    )


# fn: build_suggestions | tr: quiz sonucundan tam öneri yanıtı oluştur (ana giriş) / en: build full suggestion response from quiz result (main entry)
def build_suggestions(
    quiz_result: QuizSubmissionResponse,
    user_id: int = 1,
    output_locale: str = "en",
    document_id: Optional[str] = None,
) -> SuggestionResponse:
    """
    Personalized suggestions from this quiz's topic performance (not generic per PDF).
    """
    tr = _is_tr(output_locale)
    ta = quiz_result.topic_analysis
    topics_out: List[TopicSuggestion] = []

    for tp in ta.topics:
        st = tp.status
        if st == "moderate":
            st = "developing"
        if st in ("very_weak", "weak", "developing", "good", "strong"):
            topics_out.append(
                TopicSuggestion(
                    topic=tp.topic,
                    status=st,
                    success_rate=tp.success_rate,
                    actions=_actions_for_topic(tp, tr=tr),
                )
            )

    general_actions: List[SuggestionAction] = []
    if quiz_result.follow_up_actions:
        for msg in quiz_result.follow_up_actions:
            if msg and str(msg).strip():
                general_actions.append(
                    SuggestionAction(
                        action="review",
                        message=str(msg).strip(),
                    )
                )

    remedial = _remedial_topic_names(ta)
    if remedial:
        general_actions.insert(
            0,
            SuggestionAction(
                action="review",
                message=(
                    "Önce listedeki zayıf konuları sırayla bitir; her yanlış aşağıda konuyla eşleşiyor."
                    if tr
                    else "Work down the weak-topic list in order — each mistake below maps to a topic."
                ),
            ),
        )
    elif quiz_result.score_percentage >= 70:
        general_actions.append(
            SuggestionAction(
                action="maintenance",
                message=(
                    "Bu quizde zayıf bant yok; karışık tekrar + gelişmekte olan konulara kısa Pomodoro iyi gider."
                    if tr
                    else "No weak band on this quiz — mixed review plus short Pomodoro blocks on developing topics."
                ),
            )
        )

    if ta.strong_topics:
        general_actions.append(
            SuggestionAction(
                action="maintenance",
                message=(
                    f"Çok güçlü (90%+): {', '.join(ta.strong_topics[:5])}"
                    + (" …" if len(ta.strong_topics) > 5 else "")
                    + ". Hafif karışık quiz yeter."
                    if tr
                    else (
                        f"Very strong (90%+): {', '.join(ta.strong_topics[:5])}"
                        + (" …" if len(ta.strong_topics) > 5 else "")
                        + ". Light mixed quizzes keep them warm."
                    )
                ),
            )
        )

    coach = _maybe_ollama_coach(quiz_result, user_id, output_locale=output_locale)
    adaptive = _build_adaptive_plan(quiz_result, tr=tr)
    coaching_pack = build_coaching_pack(
        quiz_result,
        document_id=(document_id or "").strip() or None,
        output_locale=output_locale,
    )

    very_weak = list(getattr(ta, "very_weak_topics", None) or [])

    return SuggestionResponse(
        very_weak_topics=very_weak,
        weak_topics=list(ta.weak_topics),
        moderate_topics=list(ta.moderate_topics),
        developing_topics=list(ta.developing_topics),
        good_topics=list(ta.good_topics),
        strong_topics=list(ta.strong_topics),
        topics=topics_out,
        general_actions=general_actions,
        coach_message=coach,
        user_id=user_id,
        adaptive_plan=adaptive,
        coaching_pack=coaching_pack,
    )
