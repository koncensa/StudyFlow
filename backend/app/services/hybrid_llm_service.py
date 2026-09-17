# svc: hybrid_llm | tr: yerel ollama yedeği olarak bulut llm (openai uyumlu) cagir / en: cloud llm fallback when local ollama is not enough

import os
from typing import Optional, Tuple

import httpx


# fn: _truthy | tr: env degerini true/false yap / en: parse env string as boolean
def _truthy(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


# fn: cloud_enabled | tr: bulut yedek açık mı / en: is cloud fallback enabled
def cloud_enabled() -> bool:
    return _truthy(os.getenv("HYBRID_CLOUD_FALLBACK_ENABLED", "false"))


# fn: cloud_preferred | tr: önce bulut mu kullanılsın / en: prefer cloud over local ollama
def cloud_preferred() -> bool:
    return _truthy(os.getenv("HYBRID_PREFER_CLOUD", "false"))


# fn: _cloud_api_base | tr: bulut api url / en: cloud api base url
def _cloud_api_base() -> str:
    return os.getenv("HYBRID_CLOUD_API_BASE", "https://api.openai.com/v1").rstrip("/")


# fn: _cloud_api_key | tr: bulut api anahtarı / en: cloud api key
def _cloud_api_key() -> str:
    return os.getenv("HYBRID_CLOUD_API_KEY", "").strip()


# fn: _cloud_model | tr: bulut model adı / en: cloud model name
def _cloud_model() -> str:
    return os.getenv("HYBRID_CLOUD_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


# fn: _cloud_timeout | tr: istek zaman aşımı (sn) / en: request timeout seconds
def _cloud_timeout() -> float:
    raw = os.getenv("HYBRID_CLOUD_TIMEOUT_SECONDS", "35").strip()
    try:
        v = float(raw)
    except Exception:
        v = 35.0
    return max(8.0, min(v, 120.0))


# fn: provider_status | tr: bulut sağlayıcıdurumu (health check) / en: cloud provider status for health check
def provider_status() -> dict:
    enabled = cloud_enabled()
    key = _cloud_api_key()
    if not enabled:
        return {"status": "disabled", "detail": "HYBRID_CLOUD_FALLBACK_ENABLED=false"}
    if not key:
        return {"status": "misconfigured", "detail": "HYBRID_CLOUD_API_KEY is missing"}
    return {
        "status": "ready",
        "detail": None,
        "api_base": _cloud_api_base(),
        "model": _cloud_model(),
        "preferred": cloud_preferred(),
    }


# fn: cloud_chat_completion_ex | tr: bulut llm cagir, metin + durum kodu dön / en: call cloud llm, return text + status code
def cloud_chat_completion_ex(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    timeout_seconds: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[str, str]:
    if not cloud_enabled():
        return "", "disabled"
    api_key = _cloud_api_key()
    if not api_key:
        return "", "misconfigured"
    payload = {
        "model": _cloud_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": max(0.0, min(float(temperature), 1.2)),
    }
    if max_tokens is not None:
        try:
            mt = int(max_tokens)
        except (TypeError, ValueError):
            mt = 0
        if mt > 0:
            payload["max_tokens"] = max(256, min(mt, 4096))
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        to = float(timeout_seconds) if timeout_seconds is not None else _cloud_timeout()
        to = max(8.0, min(to, 180.0))
        with httpx.Client(timeout=to) as client:
            resp = client.post(
                f"{_cloud_api_base()}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return "", "empty_response"
            msg = (choices[0].get("message") or {}).get("content") or ""
            out = (msg or "").strip()
            if not out:
                return "", "empty_response"
            return out, "ok"
    except httpx.TimeoutException:
        return "", "timeout"
    except httpx.HTTPStatusError:
        return "", "http_error"
    except Exception:
        return "", "error"


# fn: cloud_chat_completion | tr: bulut llm cağır, sadece metin dö / en: call cloud llm, return text only
def cloud_chat_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
    timeout_seconds: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    text, _st = cloud_chat_completion_ex(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
    )
    return text
