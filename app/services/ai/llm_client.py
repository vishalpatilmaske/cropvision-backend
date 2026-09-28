"""Thin wrapper around the vision-capable LLM API (OpenAI GPT vision) --
see app.config.Config's OPENAI_API_KEY / OPENAI_MODEL. The rest of the codebase
never talks to the OpenAI SDK directly; callers only ever see
`generate_structured_json_from_image` / `generate_structured_json_from_text`,
so the underlying provider can keep changing without touching route or
service code.

No API keys are ever logged. Credentials come exclusively from environment
variables (see app.config.Config).
"""
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from flask import current_app

logger = logging.getLogger("cropvision.ai.llm_client")


class AIServiceError(Exception):
    """Raised for any recoverable failure talking to the vision LLM."""

    def __init__(self, message: str, code: str = "AI_SERVICE_ERROR"):
        super().__init__(message)
        self.code = code


@dataclass
class AIResponse:
    text: str
    model: str
    latency_ms: int


@dataclass
class ChatResult:
    """One assistant turn: either final text (`content`) or a request to run
    tools (`tool_calls`, each {"id", "name", "arguments": <JSON string>})."""
    content: Optional[str]
    tool_calls: List[Dict[str, Any]]
    model: str
    latency_ms: int


class LLMClient:
    """Lazily-initialized singleton-style client bound to the Flask app config."""

    def __init__(self):
        self._client = None
        self._available = False
        self._model = None

    def init_app(self, app) -> None:
        api_key = app.config.get("OPENAI_API_KEY")
        self._model = app.config.get("OPENAI_MODEL", "gpt-4o")

        if not api_key:
            logger.warning("OPENAI_API_KEY not configured -- vision analysis will be unavailable.")
            self._available = False
            return

        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key)
            self._available = True
            logger.info("OpenAI vision client initialized (model=%s).", self._model)
        except Exception:
            logger.exception("Failed to initialize OpenAI vision client.")
            self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def _classify_exception(self, exc: Exception) -> str:
        from openai import APIConnectionError, APITimeoutError, AuthenticationError, PermissionDeniedError, RateLimitError

        if isinstance(exc, RateLimitError):
            return "AI_RATE_LIMITED"
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return "AI_AUTH_FAILED"
        if isinstance(exc, APITimeoutError):
            return "AI_TIMEOUT"
        if isinstance(exc, APIConnectionError):
            return "AI_UNAVAILABLE"
        return "AI_UNAVAILABLE"

    def _create_with_retry(self, label: str, request: Dict[str, Any], timeout_seconds, max_retries):
        """Call chat.completions.create, retrying transient failures with a
        short backoff. Auth failures and rate limits are not retried. Raises
        AIServiceError for every failure category."""
        if not self._available:
            raise AIServiceError(f"{label} service is not configured.", code="AI_NOT_CONFIGURED")

        timeout_seconds = timeout_seconds or current_app.config.get("AI_REQUEST_TIMEOUT_SECONDS", 30)
        if max_retries is None:
            max_retries = current_app.config.get("AI_MAX_RETRIES", 2)

        for attempt in range(max_retries + 1):
            start = time.monotonic()
            try:
                response = self._client.chat.completions.create(
                    model=self._model, timeout=timeout_seconds, **request
                )
                return response, int((time.monotonic() - start) * 1000)
            except Exception as exc:  # noqa: BLE001 - broad by design, classified below
                code = self._classify_exception(exc)

                if code == "AI_AUTH_FAILED":
                    logger.error("%s auth failure (key redacted).", label)
                    raise AIServiceError(f"{label} authentication failed.", code=code) from exc

                logger.warning(
                    "%s call failed (attempt %s/%s, code=%s): %s",
                    label, attempt + 1, max_retries + 1, code, type(exc).__name__,
                )

                if code == "AI_RATE_LIMITED" or attempt == max_retries:
                    raise AIServiceError(
                        f"{label} service is currently unavailable. Please try again shortly.", code=code
                    ) from exc

                time.sleep(min(2 ** attempt, 5))

        raise AIServiceError(f"{label} service failed after retries.", code="AI_UNAVAILABLE")

    def _json_completion(self, label, messages, timeout_seconds, max_retries) -> AIResponse:
        response, latency_ms = self._create_with_retry(
            label,
            {"messages": messages, "response_format": {"type": "json_object"}},
            timeout_seconds,
            max_retries,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise AIServiceError(f"{label} model returned an empty response.", code="AI_EMPTY_RESPONSE")
        return AIResponse(text=text, model=self._model, latency_ms=latency_ms)

    def generate_structured_json_from_image(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_bytes: bytes,
        mime_type: str,
        timeout_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> AIResponse:
        """Send an image + prompt to the vision LLM and return the raw JSON
        text response."""
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        return self._json_completion("Vision AI", messages, timeout_seconds, max_retries)

    def generate_structured_json_from_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> AIResponse:
        """Text-only variant, for prompts that don't need a photo."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._json_completion("AI", messages, timeout_seconds, max_retries)

    def chat_completion(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> ChatResult:
        """One free-form chat turn, optionally with function-calling tools.
        Used by the farm assistant agent loop."""
        request: Dict[str, Any] = {"messages": messages}
        if tools:
            request["tools"] = tools
            if tool_choice:
                request["tool_choice"] = tool_choice

        response, latency_ms = self._create_with_retry("AI chat", request, timeout_seconds, max_retries)
        message = response.choices[0].message
        tool_calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
            for tc in (message.tool_calls or [])
        ]
        return ChatResult(
            content=(message.content or "").strip() or None,
            tool_calls=tool_calls,
            model=self._model,
            latency_ms=latency_ms,
        )


def parse_json_text(text: str) -> Dict[str, Any]:
    """Parse a model's JSON reply, tolerating a ```json fenced block."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return json.loads(cleaned)


llm_client = LLMClient()
