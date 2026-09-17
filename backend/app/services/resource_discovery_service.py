# svc: resource_discovery_service | tr: konu için youtube video ve web kaynak linki bul / en: discover youtube video and web resource links for a topic

from __future__ import annotations

import re
from typing import Dict, List
from urllib.parse import quote_plus, unquote, urlparse

import httpx


# fn: _youtube_search_url | tr: konu için youtube arama url'si üret / en: build youtube search url for topic
def _youtube_search_url(query: str) -> str:
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}"


# fn: _google_search_url | tr: konu için google arama url'si üret / en: build google search url for topic
def _google_search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}"


# fn: _extract_first_youtube_watch | tr: youtube arama html'inden ilk watch linkini çıkar / en: extract first watch link from youtube search html
def _extract_first_youtube_watch(html: str) -> str | None:
    seen: set[str] = set()
    for m in re.finditer(r'"/watch\?v=([A-Za-z0-9_-]{11})', html):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        return f"https://www.youtube.com/watch?v={vid}"
    return None


# fn: _extract_duckduckgo_urls | tr: duckduckgo html sonuçlarından url listesi çıkar / en: extract url list from duckduckgo html results
def _extract_duckduckgo_urls(html: str) -> List[str]:
    urls: List[str] = []
    for m in re.finditer(r'href="[^"]*uddg=([^"&]+)', html):
        raw = unquote(m.group(1))
        if raw.startswith("http://") or raw.startswith("https://"):
            urls.append(raw)
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        raw = m.group(1)
        if "duckduckgo.com" in raw:
            continue
        urls.append(raw)
    return urls


# fn: _prefer_educational_url | tr: url listesinden eğitim sitesini tercih et / en: pick preferred educational site from url list
def _prefer_educational_url(urls: List[str]) -> str | None:
    # tr: öncelikli eğitim domainleri / en: preferred educational domains
    preferred = [
        "wikipedia.org",
        "khanacademy.org",
        "coursera.org",
        "edx.org",
        "mit.edu",
        "stanford.edu",
        "geeksforgeeks.org",
        "towardsdatascience.com",
        "scikit-learn.org",
        "machinelearningmastery.com",
    ]
    for host_part in preferred:
        for url in urls:
            host = urlparse(url).netloc.lower()
            if host_part in host:
                return url
    for url in urls:
        host = urlparse(url).netloc.lower()
        if "youtube.com" in host or "youtu.be" in host:
            continue
        if host:
            return url
    return None


# fn: discover_best_links | tr: konu için en iyi video ve web linklerini döndür (fallback dahil) / en: return best video and web links for topic with fallbacks
def discover_best_links(topic: str, locale: str = "en") -> Dict[str, str]:
    clean_topic = (topic or "").strip()
    if not clean_topic:
        clean_topic = "study techniques" if locale != "tr" else "ders çalışma teknikleri"
    is_tr = str(locale).lower().startswith("tr")

    # tr: locale'e göre arama sorguları / en: search queries by locale
    video_query = f"{clean_topic} konu anlatımı" if is_tr else f"{clean_topic} explained"
    web_query = f"{clean_topic} konu özeti kaynak" if is_tr else f"{clean_topic} best study resource"

    video_fallback = _youtube_search_url(video_query)
    web_fallback = _google_search_url(web_query)

    out: Dict[str, str] = {
        "topic": clean_topic,
        "video_url": video_fallback,
        "web_url": web_fallback,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }

    try:
        with httpx.Client(timeout=6.5, follow_redirects=True, headers=headers) as client:
            yt_resp = client.get(video_fallback)
            best_video = _extract_first_youtube_watch(yt_resp.text or "")
            if best_video:
                out["video_url"] = best_video

            ddg_query = quote_plus(web_query)
            ddg_resp = client.get(f"https://duckduckgo.com/html/?q={ddg_query}")
            urls = _extract_duckduckgo_urls(ddg_resp.text or "")
            best_web = _prefer_educational_url(urls)
            if best_web:
                out["web_url"] = best_web
    except Exception:
        # tr: ağ hatasında fallback url'ler kalır / en: on network error keep fallback urls
        pass

    return out
