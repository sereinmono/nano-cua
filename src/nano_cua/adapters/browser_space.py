"""Browser action-space vocabulary shared by the executor and the model actor.

Both the Playwright executor and the VLM actor must agree on what a valid
browser action is, so the whitelist and parameter validation live here instead
of being duplicated in two adapters (or, worse, leaking into the core). This
module imports neither playwright nor an HTTP client.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contracts import Action, ActionValidationError, frozen_mapping

SPACE = "browser"
CLICK = "click"
TYPE_TEXT = "type_text"
PRESS_KEY = "press_key"
ACTION_KINDS: tuple[str, ...] = (CLICK, TYPE_TEXT, PRESS_KEY)

MAX_TYPE_CHARS = 2000
MAX_KEY_CHARS = 64

_MODIFIERS = frozenset({"Shift", "Control", "Alt", "Meta"})
_NAMED_KEYS = frozenset(
    {
        "Enter",
        "Escape",
        "Tab",
        "Backspace",
        "Delete",
        "Insert",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Space",
        "Shift",
        "Control",
        "Alt",
        "Meta",
        "CapsLock",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "F10",
        "F11",
        "F12",
    }
)
_ARGS: dict[str, frozenset[str]] = {
    CLICK: frozenset({"x", "y"}),
    TYPE_TEXT: frozenset({"text"}),
    PRESS_KEY: frozenset({"key"}),
}


def _check_kind(kind: Any) -> str:
    if isinstance(kind, str) and kind in ACTION_KINDS:
        return kind
    expected = ", ".join(ACTION_KINDS)
    raise ActionValidationError(f"unsupported action kind {kind!r}; expected one of: {expected}")


def _check_args(kind: str, args: Mapping[str, Any]) -> None:
    allowed = _ARGS[kind]
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ActionValidationError(f"{kind} does not accept argument(s): {', '.join(unknown)}")
    missing = sorted(allowed - set(args))
    if missing:
        raise ActionValidationError(f"{kind} is missing argument(s): {', '.join(missing)}")


def validate_click(x: Any, y: Any, *, width: int | None, height: int | None) -> tuple[int, int]:
    """Validate click coordinates; ``width``/``height`` enable viewport bounds."""
    for name, value in (("x", x), ("y", y)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ActionValidationError(f"click coordinates must be integers, got {name}={value!r}")
    if x < 0 or y < 0:
        raise ActionValidationError(f"click coordinates must be non-negative, got ({x}, {y})")
    if width is not None and height is not None and (x >= width or y >= height):
        raise ActionValidationError(f"click ({x}, {y}) is outside the {width}x{height} viewport")
    return x, y


def validate_text(text: Any) -> str:
    if not isinstance(text, str):
        raise ActionValidationError(f"type_text requires a string 'text', got {type(text).__name__}")
    if not text:
        raise ActionValidationError("type_text requires non-empty text")
    if len(text) > MAX_TYPE_CHARS:
        raise ActionValidationError(f"type_text is limited to {MAX_TYPE_CHARS} characters, got {len(text)}")
    if "\x00" in text:
        raise ActionValidationError("type_text must not contain NUL characters")
    return text


def validate_key(key: Any) -> str:
    if not isinstance(key, str):
        raise ActionValidationError(f"press_key requires a string 'key', got {type(key).__name__}")
    if not key or len(key) > MAX_KEY_CHARS:
        raise ActionValidationError(f"press_key requires 1-{MAX_KEY_CHARS} characters, got {len(key)}")
    tokens = key.split("+")
    if any(not token for token in tokens):
        raise ActionValidationError(f"invalid key combination {key!r}")
    for token in tokens[:-1]:
        if token not in _MODIFIERS:
            raise ActionValidationError(f"only modifier keys may precede '+', got {token!r} in {key!r}")
    final = tokens[-1]
    printable = len(final) == 1 and final.isprintable() and not final.isspace()
    if final not in _NAMED_KEYS and final not in _MODIFIERS and not printable:
        raise ActionValidationError(f"unsupported key {final!r} in {key!r}")
    return key


def _normalize_args(kind: str, args: Mapping[str, Any], *, width: int | None, height: int | None) -> dict[str, Any]:
    if kind == CLICK:
        x, y = validate_click(args["x"], args["y"], width=width, height=height)
        return {"x": x, "y": y}
    if kind == TYPE_TEXT:
        return {"text": validate_text(args["text"])}
    return {"key": validate_key(args["key"])}


def action_from_payload(payload: Any, *, width: int | None = None, height: int | None = None) -> Action:
    """Strictly turn a decoded JSON object into a browser Action."""
    if not isinstance(payload, Mapping):
        raise ActionValidationError(f"action must be a JSON object, got {type(payload).__name__}")
    kind = _check_kind(payload.get("kind"))
    raw_args = {key: value for key, value in payload.items() if key != "kind"}
    _check_args(kind, raw_args)
    args = _normalize_args(kind, raw_args, width=width, height=height)
    return Action(space=SPACE, kind=kind, args=frozen_mapping(args))


def normalize_action(action: Action, *, width: int | None = None, height: int | None = None) -> Action:
    """Validate an Action message coming from any policy; return a normalized copy.

    Executors use this as a last line of defence: an action that does not match
    the whitelist is rejected instead of being guessed at or partially applied.
    """
    if action.space != SPACE:
        raise ActionValidationError(f"unsupported action space {action.space!r}; this adapter executes {SPACE!r} actions")
    kind = _check_kind(action.kind)
    _check_args(kind, action.args)
    args = _normalize_args(kind, action.args, width=width, height=height)
    return Action(space=SPACE, kind=kind, args=frozen_mapping(args))
