"""The LLM's turn decision: parse and validate its JSON. Pure: no network, no FastAPI.

LLM output is untrusted input. Nothing it returns drives the interview until it passed parse_decision; on any failure the
caller falls back to move_on with FALLBACK_ACK, so a bad reply can never stall or derail a session.
"""
import json
from dataclasses import dataclass
from typing import Literal, Sequence

ACTIONS = ("follow_up", "move_on")
MAX_TEXT_CHARS = 400  # about 3 spoken sentences; a longer reply means the model ignored the format
FALLBACK_ACK = "Thanks for that answer."  # followed by the next question or the closing line, so it must fit before both


class DecisionError(ValueError):
    """The reply is not a valid decision. The message says why, for the logs (never shown to the candidate)."""


@dataclass(frozen=True)
class Decision:
    action: Literal["follow_up", "move_on"]
    text: str


def _strip_fence(raw: str) -> str:
    # Models often wrap JSON in ```json ... ``` despite being told not to; accept that one wrapper and nothing looser.
    text = raw.strip()
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        text = text[3:-3].strip()
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def parse_decision(raw: str, key_points: Sequence[str]) -> Decision:
    try:
        data = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as exc:
        raise DecisionError(f"not JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise DecisionError("not a JSON object")
    action = data.get("action")
    if action not in ACTIONS:
        raise DecisionError(f"action must be one of {', '.join(ACTIONS)}, got {action!r}")
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise DecisionError("text must be a non-empty string")
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        raise DecisionError(f"text longer than {MAX_TEXT_CHARS} characters")
    # Backs up the prompt rule "never reveal the key points". It only catches a point copied word for word; a paraphrase
    # gets through, so the prompt is still the main defence.
    lowered = text.lower()
    for point in key_points:
        if point.strip() and point.strip().lower() in lowered:
            raise DecisionError("text reveals a key point")
    return Decision(action, text)
