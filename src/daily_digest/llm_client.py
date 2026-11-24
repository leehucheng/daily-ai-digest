"""Dual-provider LLM client (Gemini + Claude) with automatic quota/error failover."""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import certifi


class LLMError(RuntimeError):
    """Raised when an LLM provider fails."""

    def __init__(self, provider: str, message: str, *, retriable_failover: bool = True) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retriable_failover = retriable_failover


@dataclass
class LLMResponse:
    provider: str
    model: str
    text: str
    parsed_json: dict[str, Any] | list[Any] | None = None
    failover_log: list[str] | None = None


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def extract_json(text: str) -> dict[str, Any] | list[Any]:
    """Extract a JSON object or array from raw LLM text (handles ```json fences)."""
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: locate outermost {...} or [...]
        start_obj = cleaned.find("{")
        end_obj = cleaned.rfind("}")
        if start_obj != -1 and end_obj > start_obj:
            return json.loads(cleaned[start_obj : end_obj + 1])
        start_arr = cleaned.find("[")
        end_arr = cleaned.rfind("]")
        if start_arr != -1 and end_arr > start_arr:
            return json.loads(cleaned[start_arr : end_arr + 1])
        raise


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 90,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


class DualLLMClient:
    """Routes calls across Gemini and Claude based on availability and quota failover.

    Modes:
    - provider="auto": tries configured priority_order (default ["gemini", "claude"]),
      skipping providers without API keys and automatically failing over on 429/402/5xx/quota errors.
    - provider="gemini": tries Gemini only (unless allow_failover=True).
    - provider="claude": tries Claude only (unless allow_failover=True).
    """

    def __init__(
        self,
        *,
        provider: str = "auto",
        priority_order: list[str] | None = None,
        gemini_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        gemini_model: str = "gemini-2.5-flash",
        claude_model: str = "claude-sonnet-5-5",
        max_tokens: int = 16384,
        timeout: int = 150,
    ) -> None:
        self.provider = provider.strip().lower()
        self.priority_order = [p.lower() for p in (priority_order or ["gemini", "claude"])]
        self.gemini_api_key = (
            gemini_api_key if gemini_api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        ).strip()
        self.anthropic_api_key = (
            anthropic_api_key
            if anthropic_api_key is not None
            else os.environ.get("ANTHROPIC_API_KEY", "")
        ).strip()
        self.gemini_model = gemini_model
        self.claude_model = claude_model
        self.max_tokens = max_tokens
        self.timeout = timeout

    def resolve_order(self) -> list[str]:
        if self.provider == "gemini":
            return ["gemini"]
        if self.provider == "claude":
            return ["claude"]
        if self.provider == "auto":
            order = [p for p in self.priority_order if p in ("gemini", "claude")]
            for fallback in ("gemini", "claude"):
                if fallback not in order:
                    order.append(fallback)
            return order
        raise ValueError(f"Unsupported LLM_PROVIDER: {self.provider!r} (expected auto|gemini|claude)")

    def available_providers(self) -> list[str]:
        avail: list[str] = []
        for p in self.resolve_order():
            if p == "gemini" and self.gemini_api_key:
                avail.append(p)
            elif p == "claude" and self.anthropic_api_key:
                avail.append(p)
        return avail

    def _call_gemini(self, system_prompt: str, user_prompt: str, *, expect_json: bool) -> LLMResponse:
        if not self.gemini_api_key:
            raise LLMError("gemini", "GEMINI_API_KEY not set")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )
        gen_cfg: dict[str, Any] = {
            "maxOutputTokens": self.max_tokens,
        }
        if expect_json:
            gen_cfg["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": gen_cfg,
        }
        try:
            data = _http_post_json(
                url,
                payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.gemini_api_key,
                },
                timeout=self.timeout,
            )
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:200]
            raise LLMError("gemini", f"HTTP {exc.code}: {err_body}") from exc
        except Exception as exc:
            raise LLMError("gemini", f"Request failed: {type(exc).__name__}") from exc

        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("gemini", f"Empty candidates: {json.dumps(data)[:300]}")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise LLMError("gemini", "Empty text response")
        parsed = extract_json(text) if expect_json else None
        return LLMResponse(provider="gemini", model=self.gemini_model, text=text, parsed_json=parsed)

    def _call_claude(self, system_prompt: str, user_prompt: str, *, expect_json: bool) -> LLMResponse:
        if not self.anthropic_api_key:
            raise LLMError("claude", "ANTHROPIC_API_KEY not set")
        url = "https://api.anthropic.com/v1/messages"
        payload: dict[str, Any] = {
            "model": self.claude_model,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            data = _http_post_json(url, payload, headers=headers, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:400]
            raise LLMError("claude", f"HTTP {exc.code}: {err_body}") from exc
        except Exception as exc:
            raise LLMError("claude", f"Request failed: {exc}") from exc

        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if not text:
            raise LLMError("claude", f"Empty text response: {json.dumps(data)[:300]}")
        if data.get("stop_reason") == "max_tokens":
            raise LLMError("claude", f"Response truncated at max_tokens={self.max_tokens} ({len(text)} chars)")
        parsed = extract_json(text) if expect_json else None
        return LLMResponse(provider="claude", model=self.claude_model, text=text, parsed_json=parsed)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        expect_json: bool = False,
    ) -> LLMResponse:
        """Invoke providers in resolved order, failing over automatically on quota/auth/5xx errors."""
        order = self.resolve_order()
        failover_log: list[str] = []
        last_error: Exception | None = None

        for name in order:
            try:
                if name == "gemini":
                    resp = self._call_gemini(system_prompt, user_prompt, expect_json=expect_json)
                elif name == "claude":
                    resp = self._call_claude(system_prompt, user_prompt, expect_json=expect_json)
                else:
                    continue
                resp.failover_log = failover_log
                return resp
            except Exception as exc:
                last_error = exc
                failover_log.append(f"{name}: {exc}")

        summary = "; ".join(failover_log) if failover_log else "No providers configured"
        raise LLMError("router", f"All LLM providers failed ({summary})") from last_error
