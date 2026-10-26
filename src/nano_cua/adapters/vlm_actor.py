"""VLM adapter: OpenAI-compatible chat-completions -> validated actions.

Model/provider details stop at this boundary. The caller injects the endpoint,
model name and optional credential; nothing here is bound to one vendor and the
core loop never imports this module. Replies are parsed strictly: only the
whitelisted browser actions below can leave this class, and malformed output is
rejected with ActionValidationError instead of being executed.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from typing import Any

import httpx

from ..contracts import (
    ActionValidationError,
    AgentContext,
    Finish,
    Observation,
    Proposal,
)
from .browser_space import action_from_payload

SCREENSHOT_KIND = "screenshot"
MAX_FINISH_REASON_CHARS = 500

DEFAULT_SYSTEM_PROMPT = (
    "You are a computer-use agent that controls one Chromium page through a tiny action API.\n"
    "Reply with exactly one JSON object and nothing else (no markdown, no prose).\n"
    "Allowed actions:\n"
    '  {"kind": "click", "x": <integer>, "y": <integer>}\n'
    '  {"kind": "type_text", "text": "<text to type into the focused element>"}\n'
    '  {"kind": "press_key", "key": "<key or key combo, e.g. Enter, Tab, Control+A>"}\n'
    '  {"kind": "finish", "reason": "<short reason>"}\n'
    "Coordinates are pixels in the supplied image and must lie inside the viewport. "
    "Never invent actions outside this list. Call finish when the task is complete."
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_json_object(text: str) -> Mapping[str, Any]:
    """Extract one JSON object from a model reply (tolerating code fences/prose)."""
    stripped = text.strip()
    fenced = _FENCE_RE.match(stripped)
    if fenced:
        stripped = fenced.group("body").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ActionValidationError(f"model reply is not a JSON object: {stripped[:120]!r}")
    candidate = stripped[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ActionValidationError(f"model reply is not valid JSON: {exc}") from None
    if not isinstance(payload, Mapping):
        raise ActionValidationError(f"model reply must contain a JSON object, got {type(payload).__name__}")
    return payload


def proposal_from_payload(
    payload: Any,
    *,
    width: int | None = None,
    height: int | None = None,
) -> Proposal:
    """Strictly convert a decoded model reply into an Action or Finish."""
    if not isinstance(payload, Mapping):
        raise ActionValidationError(f"model reply must be a JSON object, got {type(payload).__name__}")
    if payload.get("kind") == "finish":
        unknown = sorted(set(payload) - {"kind", "reason"})
        if unknown:
            raise ActionValidationError(f"finish does not accept field(s): {', '.join(unknown)}")
        reason = payload.get("reason", "")
        if not isinstance(reason, str):
            raise ActionValidationError(f"finish 'reason' must be a string, got {type(reason).__name__}")
        if len(reason) > MAX_FINISH_REASON_CHARS:
            raise ActionValidationError(f"finish 'reason' is limited to {MAX_FINISH_REASON_CHARS} characters")
        return Finish(reason=reason.strip() or "finished", success=True)
    return action_from_payload(payload, width=width, height=height)


def _observation_viewport(observation: Observation) -> tuple[int, int]:
    width = observation.metadata.get("width")
    height = observation.metadata.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise RuntimeError("screenshot observation metadata must provide positive integer 'width' and 'height'")
    return width, height


def _observation_data_url(observation: Observation) -> str:
    payload = observation.payload
    if not isinstance(payload, bytes) or not payload:
        raise RuntimeError("screenshot observation contains no image bytes")
    content_type = observation.metadata.get("content_type")
    if not isinstance(content_type, str) or "/" not in content_type:
        content_type = "image/png"
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _message_text(data: Mapping[str, Any]) -> str:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("unexpected VLM response: missing choices[0].message") from None
    if not isinstance(message, Mapping):
        raise RuntimeError("unexpected VLM response: message is not an object")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [str(part["text"]) for part in content if isinstance(part, Mapping) and isinstance(part.get("text"), str)]
        if texts:
            return "\n".join(texts)
    raise RuntimeError("VLM response contains no text content")


def _build_http_client(timeout_s: float) -> tuple[httpx.AsyncClient, bool]:
    """Create the actor's HTTP client, tolerating malformed proxy env vars.

    httpx parses ``HTTP(S)_PROXY`` / ``NO_PROXY`` eagerly; some Windows setups
    contain entries such as ``[::1]`` that httpx 0.28 rejects with InvalidURL.
    Falling back to a client that ignores environment proxies keeps the demo
    runnable instead of failing during construction.
    """
    timeout = httpx.Timeout(timeout_s)
    try:
        return httpx.AsyncClient(timeout=timeout), False
    except httpx.InvalidURL:
        return httpx.AsyncClient(timeout=timeout, trust_env=False), True


class VLMActor:
    """Chat-completions VLM behind the Actor port.

    ``base_url`` is an OpenAI-compatible endpoint root (for example
    ``https://host/v1``) or the full ``.../chat/completions`` URL. The optional
    ``client`` argument injects an ``httpx.AsyncClient`` (tests, connection
    reuse); when omitted the actor owns and must close its client.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        json_mode: bool = False,
        max_tokens: int = 512,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not model.strip():
            raise ValueError("model must be a non-empty string")
        normalized = base_url.rstrip("/")
        self._endpoint = normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._system_prompt = system_prompt
        self._json_mode = json_mode
        self._max_tokens = max_tokens
        if client is not None:
            self._client = client
            self._owns_client = False
            self._proxy_env_ignored = False
        else:
            self._client, self._proxy_env_ignored = _build_http_client(timeout_s)
            self._owns_client = True

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def model(self) -> str:
        return self._model

    @property
    def proxy_env_ignored(self) -> bool:
        """True when malformed proxy env vars forced a client without them."""
        return self._proxy_env_ignored

    async def propose(self, context: AgentContext) -> Proposal:
        observation = context.last_observation
        if observation is None:
            raise RuntimeError("VLMActor needs an observation in the context; ensure the policy observes first")
        if observation.kind != SCREENSHOT_KIND:
            raise RuntimeError(f"VLMActor only understands {SCREENSHOT_KIND!r} observations, got {observation.kind!r}")
        width, height = _observation_viewport(observation)
        messages = self._build_messages(context, observation, width=width, height=height)
        data = await self._post(messages)
        return proposal_from_payload(parse_json_object(_message_text(data)), width=width, height=height)

    def _build_messages(
        self,
        context: AgentContext,
        observation: Observation,
        *,
        width: int,
        height: int,
    ) -> list[dict[str, Any]]:
        lines = [
            f"Task: {context.task}",
            f"Action number: {context.action_count + 1} (actions executed so far: {context.action_count})",
            f"Viewport: {width}x{height} pixels; the image is exactly one screenshot of the page.",
            f"Current URL: {observation.metadata.get('url', 'unknown')}",
        ]
        if context.last_action is not None:
            lines.append(f"Last action: {context.last_action.space}.{context.last_action.kind} {dict(context.last_action.args)}")
        if context.last_result is not None:
            if context.last_result.success:
                lines.append("Last action result: ok")
            else:
                lines.append(f"Last action result: failed: {context.last_result.error}")
        lines.append("Decide the next single action. Reply with one JSON object only.")
        return [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "\n".join(lines)},
                    {"type": "image_url", "image_url": {"url": _observation_data_url(observation)}},
                ],
            },
        ]

    async def _post(self, messages: list[dict[str, Any]]) -> Mapping[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await self._client.post(self._endpoint, json=body, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            excerpt = " ".join(exc.response.text[:300].split())
            raise RuntimeError(f"VLM request failed with HTTP {exc.response.status_code}: {excerpt}") from None
        except httpx.HTTPError as exc:
            raise RuntimeError(f"VLM request failed: {type(exc).__name__}: {exc}") from None
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"VLM response is not JSON: {exc}") from None
        if not isinstance(data, Mapping):
            raise RuntimeError("VLM response must be a JSON object")
        return data

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> VLMActor:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
