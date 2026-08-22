"""Thin shared Groq client for the watershed module (photo classification +
narrative generation), per the decided provider: **Groq free tier, model
``qwen/qwen3.6-27b``** (multimodal: vision + text).

One helper, two call shapes:

* :func:`text_chat`  - plain string prompt -> completion text.
* :func:`vision_chat` - prompt + image bytes -> completion text. Large images
  are downscaled/re-encoded before base64 so we spend fewer of the free-tier
  tokens/quota on pixels.

Rate limits are honored dynamically, never hardcoded into logic: free-tier
caps (~30 RPM / 1K RPD / 8K TPM / 200K TPD for this model tier as of
2026-08) change over time, so the client simply retries 429s with the
server-provided ``retry-after`` delay plus bounded exponential backoff.
The API key is read from ``GROQ_API_KEY`` (never hardcoded); the model id
from ``GROQ_MODEL`` with the agreed default.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import random
import time
from typing import Any

logger = logging.getLogger("hackpreneur")

#: The decided provider/model (constraint #5). Override via ``GROQ_MODEL``
#: only if Groq renames the deployment — do not silently change providers.
DEFAULT_GROQ_MODEL = "qwen/qwen3.6-27b"

#: Vision preprocessing: photos are field snapshots, not evidence — cap the
#: long edge and JPEG quality so each classification costs minimal tokens.
VISION_MAX_EDGE_PX = 768
VISION_JPEG_QUALITY = 80

#: Bounded retry ladder for rate limits / transient errors.
MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 8.0
#: Cap on honoring a server ``retry-after`` so an HTTP request never wedges.
RETRY_AFTER_CAP_S = 20.0

_CLIENT: Any = None


class GroqError(RuntimeError):
    """A Groq chat-completions call failed after retries."""


class GroqNotConfiguredError(GroqError):
    """``GROQ_API_KEY`` is not set — callers should degrade gracefully."""


def _client() -> Any:
    """Lazily create the Groq client; raise when no API key is configured."""
    global _CLIENT
    if _CLIENT is None:
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise GroqNotConfiguredError(
                "GROQ_API_KEY is not set — LLM features (photo classification, "
                "narratives) are unavailable."
            )
        from groq import Groq

        _CLIENT = Groq(api_key=api_key)
    return _CLIENT


def reset_client() -> None:
    """Drop the cached client (used by tests / after changing credentials)."""
    global _CLIENT
    _CLIENT = None


def model_id() -> str:
    """The active Groq model id (env-overridable, agreed default)."""
    return os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)


def _chat_with_retry(messages: list[dict[str, Any]], *, max_tokens: int, timeout_s: float) -> str:
    """One chat completion with bounded retry/backoff on transient errors.

    Retries only what retrying can fix: 429 rate limits (honoring the
    server's ``retry-after``, capped) and 5xx/connection errors. Auth and
    schema errors surface immediately.
    """
    client = _client()
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.chat.completions.create(
                model=model_id(),
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
                timeout=timeout_s,
                # Qwen3 reasoning models: disable chain-of-thought entirely —
                # vision reasoning otherwise burns the whole token budget
                # before any answer is emitted. (Groq-specific extension;
                # "/no_think" in prompts and think-block stripping in the
                # classifier remain as defense in depth.)
                reasoning_effort="none",
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 — narrowed below by class name
            last_exc = exc
            wait_s = _retry_wait_for(exc, attempt)
            if wait_s is None or attempt == MAX_ATTEMPTS:
                break
            logger.warning(
                "groq call attempt %d/%d failed (%s) — retrying in %.1fs",
                attempt, MAX_ATTEMPTS, type(exc).__name__, wait_s,
            )
            time.sleep(wait_s)
    raise GroqError(f"Groq call failed after {attempt} attempt(s): {last_exc}") from last_exc


def _retry_wait_for(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before a retry, or ``None`` when not retryable."""
    status = getattr(getattr(exc, "status_code", None), "__int__", lambda: None)()
    retry_after = _header_retry_after(exc)
    name = type(exc).__name__
    if name == "RateLimitError":  # 429 — honor server guidance when present
        return min(retry_after if retry_after else BACKOFF_BASE_S * 2 ** (attempt - 1),
                   RETRY_AFTER_CAP_S)
    if retry_after is not None:  # any error carrying a retry-after header
        return min(retry_after, RETRY_AFTER_CAP_S)
    if (status is not None and status >= 500) or name in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
    }:
        return min(BACKOFF_BASE_S * 2 ** (attempt - 1), BACKOFF_CAP_S) + random.uniform(0, 0.5)
    return None


def _header_retry_after(exc: Exception) -> float | None:
    """Extract a numeric ``retry-after`` from the SDK exception, if attached."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    raw = headers.get("retry-after") if headers else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def text_chat(prompt: str, *, system: str | None = None, max_tokens: int = 512,
              timeout_s: float = 30.0) -> str:
    """Text-only completion for ``prompt`` (the narrative-generator path)."""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return _chat_with_retry(messages, max_tokens=max_tokens, timeout_s=timeout_s)


def vision_chat(
    prompt: str,
    image_bytes: bytes,
    *,
    mime_type: str = "image/jpeg",
    system: str | None = None,
    max_tokens: int = 512,
    timeout_s: float = 45.0,
) -> str:
    """Vision completion for ``prompt`` over ``image_bytes`` (classifier path).

    The image travels as a base64 data URL per the OpenAI-compatible content
    block shape; it is downscaled/re-encoded first to conserve free-tier quota.
    """
    data_url = f"data:{mime_type};base64,{base64.b64encode(_downscaled_jpeg(image_bytes)).decode('ascii')}"
    content: list[dict[str, Any]] = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    return _chat_with_retry(messages, max_tokens=max_tokens, timeout_s=timeout_s)


def _downscaled_jpeg(image_bytes: bytes) -> bytes:
    """Re-encode to a compact RGB JPEG capped at ``VISION_MAX_EDGE_PX``.

    Falls back to the original bytes when Pillow cannot process them (the
    downstream API call will then surface its own decode error).
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((VISION_MAX_EDGE_PX, VISION_MAX_EDGE_PX))
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=VISION_JPEG_QUALITY)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 — never let cosmetic downscaling fail a call
        return image_bytes


__all__ = [
    "GroqError",
    "GroqNotConfiguredError",
    "DEFAULT_GROQ_MODEL",
    "model_id",
    "reset_client",
    "text_chat",
    "vision_chat",
]
