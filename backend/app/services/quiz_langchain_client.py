# svc: quiz_langchain_client | tr: quiz soru üretimi için llm tamamlama (bulut veya ollama) / en: llm completion for quiz generation (cloud or ollama)

from __future__ import annotations

import logging
import os
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_fixed

_log = logging.getLogger(__name__)


# fn: _truthy | tr: ortam değişkeni doğru/yanlış mı / en: is env var truthy
def _truthy(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


# fn: _quiz_provider | tr: quiz llm sağlayıcısı (auto, ollama, openai) / en: quiz llm provider (auto, ollama, openai)
def _quiz_provider() -> str:
    return os.getenv("QUIZ_PROVIDER", "auto").strip().lower()


# fn: _quiz_cloud_timeout | tr: bulut api zaman aşımı (saniye) / en: cloud api timeout seconds
def _quiz_cloud_timeout() -> float:
    try:
        return float(os.getenv("QUIZ_CLOUD_TIMEOUT_SECONDS", "90").strip() or "90")
    except ValueError:
        return 90.0


# fn: _should_try_cloud_first | tr: önce bulut llm denenmeli mi / en: should try cloud llm first
def _should_try_cloud_first() -> bool:
    prov = _quiz_provider()
    if prov == "openai":
        return True
    if prov == "ollama":
        return False
    try:
        from app.services.hybrid_llm_service import cloud_enabled, cloud_preferred

        if not cloud_enabled():
            return False
        if cloud_preferred():
            return True
    except Exception:
        pass
    return _truthy(os.getenv("QUIZ_PREFER_OPENAI", "false"))


# fn: _should_try_cloud_fallback | tr: ollama başarısız olursa buluta düşülmeli mi / en: fallback to cloud if ollama fails
def _should_try_cloud_fallback() -> bool:
    prov = _quiz_provider()
    if prov == "ollama":
        return False
    try:
        from app.services.hybrid_llm_service import cloud_enabled

        return bool(cloud_enabled())
    except Exception:
        return False


# fn: _cloud_quiz_complete | tr: bulut llm ile quiz json metni üret / en: generate quiz json text via cloud llm
def _cloud_quiz_complete(
    user_prompt: str,
    system: Optional[str],
    *,
    temperature: float,
) -> str:
    try:
        from app.services.hybrid_llm_service import cloud_chat_completion, cloud_enabled

        if not cloud_enabled():
            return ""
        sys_line = (
            system.strip()
            if system and system.strip()
            else "You follow instructions exactly and return only the JSON or text the user asked for."
        )
        to = max(25.0, min(_quiz_cloud_timeout(), 180.0))
        out = cloud_chat_completion(
            system_prompt=sys_line,
            user_prompt=user_prompt,
            temperature=float(temperature),
            timeout_seconds=to,
        )
        return (out or "").strip()
    except Exception as exc:
        _log.debug("quiz cloud completion failed: %s", exc)
        return ""


# fn: _quiz_llm_attempts | tr: llm çağrısı tekrar sayısı / en: llm call retry count
def _quiz_llm_attempts() -> int:
    try:
        n = int(os.getenv("QUIZ_LLM_RETRIES", "4").strip() or "4")
    except ValueError:
        n = 4
    return max(2, min(n, 6))


# fn: _use_langchain | tr: langchain ollama istemcisi kullanılsın mı / en: use langchain ollama client
def _use_langchain() -> bool:
    return os.getenv("QUIZ_USE_LANGCHAIN", "1").strip().lower() not in ("0", "false", "no", "off")


# fn: quiz_model_generate | tr: quiz için tek llm tamamlama (bulut → langchain → httpx) / en: single llm completion for quiz (cloud → langchain → httpx)
@retry(
    reraise=True,
    stop=stop_after_attempt(_quiz_llm_attempts()),
    wait=wait_fixed(0.35),
)
def quiz_model_generate(
    user_prompt: str,
    system: Optional[str] = None,
    *,
    temperature: float = 0.25,
    timeout_seconds: float = 14.0,
    provider_override: Optional[str] = None,
) -> str:
    prov = (provider_override or _quiz_provider()).strip().lower()
    tried_cloud_first = False

    # tr: sadece bulut modu / en: cloud-only mode
    if prov == "openai":
        cloud_out = _cloud_quiz_complete(user_prompt, system, temperature=temperature)
        return cloud_out if len(cloud_out) > 8 else ""

    should_try_cloud_first = False
    if prov == "auto":
        should_try_cloud_first = _should_try_cloud_first()
    elif prov == "openai":
        should_try_cloud_first = True

    if should_try_cloud_first:
        tried_cloud_first = True
        cloud_out = _cloud_quiz_complete(user_prompt, system, temperature=temperature)
        if len(cloud_out) > 8:
            return cloud_out

    ollama_out = ""
    # tr: langchain ChatOllama ile yerel model / en: local model via langchain ChatOllama
    if _use_langchain():
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_ollama import ChatOllama

            from app.services.ollama_service import _base_url, _quiz_model

            model = _quiz_model()
            base = _base_url()
            llm = ChatOllama(
                model=model,
                base_url=base,
                temperature=float(temperature),
                timeout=float(timeout_seconds),
            )
            msgs = []
            if system and system.strip():
                msgs.append(SystemMessage(content=system.strip()))
            msgs.append(HumanMessage(content=user_prompt))
            out = llm.invoke(msgs)
            text = getattr(out, "content", None) or str(out)
            if text and len(str(text).strip()) > 8:
                return str(text).strip()
        except Exception as e:
            _log.debug("quiz_langchain_client: falling back to ollama_chat (%s)", e)

    # tr: yedek: doğrudan httpx ollama istemcisi / en: fallback: direct httpx ollama client
    from app.services.ollama_service import _quiz_model, ollama_chat

    ollama_out = (
        ollama_chat(
            user_prompt,
            system=system,
            options={"temperature": float(temperature)},
            timeout_seconds=float(timeout_seconds),
            model=_quiz_model(),
        )
        or ""
    ).strip()

    if len(ollama_out) > 8:
        return ollama_out

    # tr: ollama boş döndüyse buluta son çare / en: last resort cloud if ollama returned empty
    if not tried_cloud_first and ((prov == "auto" and _should_try_cloud_fallback()) or prov == "openai"):
        cloud_out = _cloud_quiz_complete(user_prompt, system, temperature=temperature)
        if len(cloud_out) > 8:
            return cloud_out

    return ""
