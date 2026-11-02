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
    f"{SUMMARY_TAG} You are an experienced interview coach writing feedback for a candidate after a mock interview. "
    "You are not interviewing any more: never ask a question. Use only what is in the conversation and do not invent "
    "answers the candidate did not give."
)
# The request is a final USER turn on purpose. Put in the system prompt alone, the instruction lost: the conversation ended
# with the coach's own question, and the model carried on as the coach and asked another question instead of summarising.
_SUMMARY_REQUEST = (
    "The interview is over. Do not ask another question. Write the candidate's feedback now, in plain text with no "
    "markdown, using exactly this layout with a blank line between the parts:\n"
    "Summary: <what was discussed and how the candidate did, in 2 sentences>\n"
    "Strengths: <what the candidate did well, in 1 or 2 sentences>\n"
    "Areas to improve: <one or two specific things to practise, in 1 or 2 sentences>\n"
    "If the candidate gave little or no real answer, say so plainly in the Summary and keep the other two parts short."
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
    """Summaries see the whole conversation (bounded already by the 60 s per-utterance cap and session length), then a final
    user turn with the request. The history of an ended interview ends with the coach's message, so roles still alternate."""
    return [Message("system", _SUMMARY_SYSTEM), Message("user", _KICKOFF), *history, Message("user", _SUMMARY_REQUEST)]
