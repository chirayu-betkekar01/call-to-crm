"""Minimal OpenRouter chat-completions client with JSON-schema structured output."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"


class LLMError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, timeout: int = 600):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise LLMError("OPENROUTER_API_KEY is not set (put it in .env).")
        self.model = model or os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.last_usage: dict = {}

    def _post(self, body: dict, retries: int = 3) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "transcript-people-agent",
        }
        for attempt in range(retries + 1):
            r = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=self.timeout)
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** (attempt + 1)))
                continue
            if not r.ok:
                raise LLMError(f"OpenRouter {r.status_code}: {r.text[:500]}")
            data = r.json()
            if "error" in data:
                raise LLMError(f"OpenRouter error: {data['error']}")
            return data
        raise LLMError("OpenRouter kept failing after retries")

    def complete_json(self, system: str, user: str, schema: dict, schema_name: str = "output", max_tokens: int = 16000) -> dict:
        """Ask for JSON matching `schema`. Uses strict structured output; falls back to
        prompt-only JSON if the routed provider rejects `response_format`."""
        base = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        structured = {
            **base,
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            "provider": {"require_parameters": True},
        }
        try:
            data = self._post(structured)
        except LLMError as e:
            if "response_format" not in str(e) and "json_schema" not in str(e) and "No endpoints" not in str(e):
                raise
            fallback_user = user + "\n\nReturn ONLY a JSON object matching this JSON schema, no prose:\n" + json.dumps(schema)
            data = self._post({**base, "messages": [base["messages"][0], {"role": "user", "content": fallback_user}]})

        self.last_usage = data.get("usage") or {}
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise LLMError("Model output was cut off (max_tokens). Raise --max-tokens.")
        content = (choice.get("message") or {}).get("content") or ""
        return _parse_json(content)


def _parse_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise LLMError(f"Model did not return JSON: {text[:300]}")
