"""Opt-in, model-specific input token counts through OpenAI's count endpoint."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

DEFAULT_COUNT_ENDPOINT = "https://api.openai.com/v1/responses/input_tokens"
def _configured_timeout() -> float:
    try:
        return min(300.0, max(1.0, float(os.getenv("OPENAI_TOKEN_COUNT_TIMEOUT_SECONDS", "45"))))
    except (TypeError, ValueError):
        return 45.0


COUNT_TIMEOUT_SECONDS = _configured_timeout()
SUPPORTED_MODELS = {"gpt-4.1", "gpt-4o"}
COUNTING_INSTRUCTION = "Count these inputs as model input. Do not summarize, interpret, or answer them."
COUNTING_METHOD = "OpenAI Responses input token count endpoint; direct PDF input and text input"
_COUNT_CACHE: OrderedDict[str, "TokenCount"] = OrderedDict()
_CACHE_MAX_ENTRIES = 512


@dataclass(frozen=True)
class TokenCount:
    status: str
    input_tokens: Optional[int]
    model: str
    method: str
    timestamp: Optional[str]
    error: Optional[str] = None
    cached: bool = False

    def as_response(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "inputTokens": self.input_tokens,
            "model": self.model,
            "method": self.method,
            "timestamp": self.timestamp,
            "error": self.error,
            "cached": self.cached,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_result(status: str, model: str, message: Optional[str] = None) -> TokenCount:
    return TokenCount(status, None, model, COUNTING_METHOD, None, message)


def unavailable_count_result(model: str, message: str) -> dict[str, Any]:
    """Build a safe no-count result if optional counting fails outside its transport."""
    pdf_count = _empty_result("API unavailable", model, message)
    markdown_count = _empty_result("API unavailable", model, message)
    return {
        "originalPdf": pdf_count.as_response(),
        "markdown": markdown_count.as_response(),
        "comparison": compare_api_counts(None, None),
        "status": "API unavailable",
        "model": model,
        "method": COUNTING_METHOD,
    }


def compare_api_counts(original: Optional[int], markdown: Optional[int]) -> dict[str, Any]:
    if original is None or markdown is None:
        return {"status": "Not comparable", "tokensSaved": None, "reductionPercent": None,
                "reason": "Both API verified counts are required for comparison."}
    if original == 0:
        return {"status": "Not comparable", "tokensSaved": None, "reductionPercent": None,
                "reason": "The API returned zero original PDF input tokens, so a percentage cannot be calculated."}
    saved = original - markdown
    return {"status": "API verified", "tokensSaved": saved,
            "reductionPercent": round(100 * saved / original, 2), "reason": None}


def _cache_key(kind: str, content_hash: str, model: str, endpoint: str) -> str:
    return hashlib.sha256(f"{kind}:{content_hash}:{model}:{endpoint}".encode()).hexdigest()


def clear_count_cache() -> None:
    _COUNT_CACHE.clear()


async def _request_count(
    *, api_key: str, model: str, input_items: list[dict[str, Any]], endpoint: str,
    timeout: float, transport: Optional[httpx.AsyncBaseTransport] = None,
) -> TokenCount:
    now = _now()
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "input": [{"role": "user", "content": input_items}]},
            )
    except httpx.TimeoutException:
        return TokenCount("API unavailable", None, model, COUNTING_METHOD, now,
                          f"OpenAI token counting timed out after {timeout:g} seconds.")
    except (httpx.RequestError, ValueError):
        return TokenCount("API unavailable", None, model, COUNTING_METHOD, now,
                          "Could not connect to the configured OpenAI token-count endpoint; check its URL and network access.")

    if response.status_code == 429:
        return TokenCount("API unavailable", None, model, COUNTING_METHOD, now,
                          "OpenAI rate limit reached. Try token counting again later.")
    if response.status_code in (400, 404, 422):
        # Do not return raw provider error bodies, which could contain request details.
        try:
            error_data = response.json().get("error", {})
            error_code = str(error_data.get("code", "")).lower()
            error_type = str(error_data.get("type", "")).lower()
        except (ValueError, AttributeError):
            error_code = error_type = ""
        if error_code in {"model_not_found", "unsupported_model"} or error_type == "model_not_found":
            return TokenCount("Unsupported model", None, model, COUNTING_METHOD, now,
                              f"OpenAI does not support model {model} for this count request.")
        return TokenCount("API unavailable", None, model, COUNTING_METHOD, now,
                          f"OpenAI rejected the token-count request (HTTP {response.status_code}); verify the model and PDF input.")
    if response.status_code in (401, 403):
        return TokenCount("API unavailable", None, model, COUNTING_METHOD, now,
                          "OpenAI rejected the configured API key or its permissions.")
    if response.is_error:
        return TokenCount("API unavailable", None, model, COUNTING_METHOD, now,
                          f"OpenAI token counting failed (HTTP {response.status_code}).")
    try:
        count = response.json().get("input_tokens")
    except (ValueError, AttributeError):
        count = None
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return TokenCount("API unavailable", None, model, COUNTING_METHOD, now,
                          "OpenAI returned no valid input_tokens count.")
    return TokenCount("API verified", count, model, COUNTING_METHOD, now)


async def _cached_count(
    *, kind: str, content_hash: str, model: str, endpoint: str,
    api_key: str, input_items: list[dict[str, Any]], timeout: float,
    transport: Optional[httpx.AsyncBaseTransport],
) -> TokenCount:
    key = _cache_key(kind, content_hash, model, endpoint)
    cached = _COUNT_CACHE.get(key)
    if cached:
        _COUNT_CACHE.move_to_end(key)
        return TokenCount(cached.status, cached.input_tokens, cached.model, cached.method,
                          cached.timestamp, cached.error, True)
    result = await _request_count(api_key=api_key, model=model, input_items=input_items,
                                  endpoint=endpoint, timeout=timeout, transport=transport)
    if result.status == "API verified":
        _COUNT_CACHE[key] = result
        _COUNT_CACHE.move_to_end(key)
        while len(_COUNT_CACHE) > _CACHE_MAX_ENTRIES:
            _COUNT_CACHE.popitem(last=False)
    return result


async def count_pdf_and_markdown(
    *, pdf_bytes: bytes, filename: str, markdown: str, model: str,
    consent: bool, api_key: Optional[str], endpoint: Optional[str] = None,
    timeout: Optional[float] = None, transport: Optional[httpx.AsyncBaseTransport] = None,
) -> dict[str, Any]:
    """Count inputs only after explicit consent; counting errors never affect local Markdown."""
    endpoint = endpoint or os.getenv("OPENAI_TOKEN_COUNT_URL", DEFAULT_COUNT_ENDPOINT)
    timeout = COUNT_TIMEOUT_SECONDS if timeout is None else timeout
    if not consent:
        pdf_count = _empty_result("Waiting for consent", model, "API counting not enabled; grant consent to send both inputs to OpenAI.")
        md_count = _empty_result("Waiting for consent", model, "API counting not enabled; grant consent to send both inputs to OpenAI.")
    elif not api_key:
        pdf_count = _empty_result("API key missing", model, "Set OPENAI_API_KEY on the backend to enable API counting.")
        md_count = _empty_result("API key missing", model, "Set OPENAI_API_KEY on the backend to enable API counting.")
    elif model not in SUPPORTED_MODELS:
        pdf_count = _empty_result("Unsupported model", model, "Choose a model documented for PDF inputs and input-token counting.")
        md_count = _empty_result("Unsupported model", model, "Choose a model documented for PDF inputs and input-token counting.")
    else:
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        md_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        pdf_data_url = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")
        pdf_items = [
            {"type": "input_text", "text": COUNTING_INSTRUCTION},
            {"type": "input_file", "filename": filename, "file_data": pdf_data_url, "detail": "high"},
        ]
        md_items = [
            {"type": "input_text", "text": COUNTING_INSTRUCTION},
            {"type": "input_text", "text": markdown},
        ]
        try:
            pdf_count, md_count = await asyncio.gather(
                _cached_count(kind="pdf", content_hash=pdf_hash, model=model, endpoint=endpoint,
                              api_key=api_key, input_items=pdf_items, timeout=timeout, transport=transport),
                _cached_count(kind="markdown", content_hash=md_hash, model=model, endpoint=endpoint,
                              api_key=api_key, input_items=md_items, timeout=timeout, transport=transport),
            )
        except Exception:
            pdf_count = _empty_result("API unavailable", model, "Token counting failed unexpectedly; local Markdown remains available.")
            md_count = _empty_result("API unavailable", model, "Token counting failed unexpectedly; local Markdown remains available.")

    comparison = compare_api_counts(pdf_count.input_tokens, md_count.input_tokens)
    if not consent:
        overall = "Waiting for consent"
    elif not api_key:
        overall = "API key missing"
    elif comparison["status"] == "API verified":
        overall = "API verified"
    elif pdf_count.status == "API verified" and md_count.status == "API verified":
        overall = "Not comparable"
    elif "Unsupported model" in (pdf_count.status, md_count.status):
        overall = "Unsupported model"
    else:
        overall = "API unavailable"
    return {
        "originalPdf": pdf_count.as_response(),
        "markdown": md_count.as_response(),
        "comparison": comparison,
        "status": overall,
        "model": model,
        "method": COUNTING_METHOD,
    }
