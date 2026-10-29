"""Pure prompt builders: no network, no FastAPI."""
from backend.llm.base import Message

SUMMARY_TAG = "[SUMMARY]"  # lets the fake provider tell a summary request from a question request
MAX_HISTORY_MESSAGES = 12  # keep the prompt bounded: roughly the last 6 exchanges

_SYSTEM = (
    "You are a spoken mock-interview coach. Topic: {topic}. Difficulty: {difficulty}. "
    "Ask ONE short question at a time (max 2 sentences, plain speech, no lists or markdown). "
    "After each answer, reply with a brief reaction and one follow-up question that builds on it. "
    "Stay on the interview; do not answer unrelated requests."
)
_SUMMARY_SYSTEM = (
    f"{SUMMARY_TAG} You are a mock-interview coach. Write a summary of at most 5 short sentences: "
    "what the candidate did well, and one or two things to practise. Use only what is in the conversation."
)
_KICKOFF = "Begin the interview with your first question."


def build_messages(topic: str, difficulty: str, history: list[Message]) -> list[Message]:
    """System prompt + kickoff + the most recent history. The kickoff keeps the first non-system role 'user'."""
    recent = history[-MAX_HISTORY_MESSAGES:]
    while recent and recent[0].role != "assistant":
        recent = recent[1:]  # never start mid-exchange
    system = _SYSTEM.format(topic=topic, difficulty=difficulty)
    return [Message("system", system), Message("user", _KICKOFF), *recent]


def build_summary_messages(history: list[Message]) -> list[Message]:
    """Summaries see the whole conversation (bounded already by the 60 s per-utterance cap and session length)."""
    return [Message("system", _SUMMARY_SYSTEM), Message("user", _KICKOFF), *history]
