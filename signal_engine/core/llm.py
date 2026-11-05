"""One small wrapper over Anthropic or OpenAI that returns parsed JSON.

Provider is picked from the environment: ANTHROPIC_API_KEY first, then OPENAI_API_KEY.
With neither (or in offline mode), `available` is False and callers use their fallback.
"""

from __future__ import annotations

import json
import os
import re


JSON_RULES = (
    "\n\nOutput rules: reply with one JSON object and nothing else. Keep every string on one line. "
    "Never put double quotes inside a string value; use single quotes when quoting the website. "
    "Keep each evidence string under 200 characters."
)


class BudgetExceeded(Exception):
    pass


class LLM:
    def __init__(self, offline: bool = False, max_calls: int = 300) -> None:
        self.max_calls = max_calls
        self.calls = 0
        self.provider: str | None = None
        self._client = None
        if offline:
            return
        if os.getenv("ANTHROPIC_API_KEY"):
            import anthropic

            self._client = anthropic.Anthropic()
            self.provider = "anthropic"
            self.model = os.getenv("SE_ANTHROPIC_MODEL", "claude-haiku-5-5")
        elif os.getenv("OPENAI_API_KEY"):
            import openai

            self._client = openai.OpenAI()
            self.provider = "openai"
            self.model = os.getenv("SE_OPENAI_MODEL", "gpt-4o-mini")

    @property
    def available(self) -> bool:
        return self.provider is not None

    def complete_json(self, system: str, prompt: str, max_tokens: int = 2000) -> dict:
        """Ask for JSON; if the reply doesn't parse, show the model its error and retry once."""
        if not self.available:
            raise RuntimeError("No LLM configured")
        system = system + JSON_RULES
        messages = [{"role": "user", "content": prompt}]
        last_error: Exception | None = None
        for _ in range(2):
            text = self._call(system, messages, max_tokens)
            try:
                return parse_json(text)
            except ValueError as exc:  # json.JSONDecodeError is a ValueError
                last_error = exc
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": (
                        f"That was not valid JSON ({exc}). Reply again with only the corrected JSON object. "
                        "Do not use double quotes inside string values; use single quotes instead."
                    )},
                ]
        raise ValueError(f"Model returned invalid JSON twice: {last_error}")

    def _call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call limit reached ({self.max_calls})")
        self.calls += 1
        if self.provider == "anthropic":
            resp = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system, messages=messages
            )
            return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, *messages],
        )
        return resp.choices[0].message.content or ""


def parse_json(text: str) -> dict:
    """Parse the first JSON object in a model response, tolerating code fences."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model output: {text[:200]}")
    return json.loads(text[start : end + 1])
