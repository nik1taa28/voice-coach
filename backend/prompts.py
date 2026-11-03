"""Pure prompt builders: no network, no FastAPI."""
from backend.llm.base import Message
from backend.questions import Question

SUMMARY_TAG = "[SUMMARY]"  # lets the fake provider tell a summary request from a question request
MAX_HISTORY_MESSAGES = 12  # keep the prompt bounded: roughly the last 6 exchanges

_SYSTEM = (
    "You are a spoken mock-interview coach. Topic: {topic}. Difficulty: {difficulty}. "
    "Ask ONE short question at a time (max 2 sentences, plain speech, no lists or markdown). "
    "After each answer, reply with a brief reaction and one follow-up question that builds on it. "
    "Stay on the interview; do not answer unrelated requests."
)
# Used when a bank question is being discussed. The question and key points live in the system prompt, not the history:
# MAX_HISTORY_MESSAGES can trim the original question out of a long conversation.
_FOLLOWUP_SYSTEM = (
    "You are a spoken mock-interview coach. Topic: {topic}. Difficulty: {difficulty}. "
    "The question being discussed is: {question} "
    "A strong answer covers these key points: {points}. "
    "Use the key points only to judge the candidate's answer. Never read them out and never give the answer away. "
    "After each answer, give a brief reaction in one sentence, then ask exactly ONE short follow-up question that probes "
    "a key point the candidate missed or covered only shallowly. "
    "Use plain speech, at most 3 sentences in total, no lists or markdown. "
    "Stay on this question and do not answer unrelated requests."
)
_SUMMARY_SYSTEM = (
    "{tag} You are an experienced interview coach writing feedback for a candidate after a mock interview. "
    "Topic: {topic}. Difficulty: {difficulty}. Judge the answers against that level, not against a perfect answer. "
    "You are not interviewing any more: never ask a question. Use only what is in the conversation and do not invent "
    "answers the candidate did not give. "
    "If the last coach message is a question with no candidate reply after it, the interview ended before it could be "
    "answered: do not mention it or count it against the candidate. "
    "The candidate's words come from speech recognition and may contain mishearings: when a sentence is garbled or "
    "unclear, do not claim the candidate said something specific or wrong; say it was unclear instead."
)
# The request is a final USER turn on purpose. Put in the system prompt alone, the instruction lost: the conversation ended
# with the coach's own question, and the model carried on as the coach and asked another question instead of summarising.
_SUMMARY_REQUEST = (
    "The interview is over. Do not ask another question. Write the candidate's feedback now, in plain text with no "
    "markdown, using exactly this layout with a blank line between the parts:\n"
    "Summary: <what was discussed and how the candidate did, in 2 sentences>\n"
    "Strengths: <what the candidate did well, in 1 or 2 sentences>\n"
    "Areas to improve: <one or two specific things to practise, in 1 or 2 sentences>\n"
    "Practice next: <one concrete exercise or topic to practise before the next session, in 1 sentence>\n"
    "If the candidate gave little or no real answer, say so plainly in the Summary and keep the other three parts short."
)
_KICKOFF = "Begin the interview with your first question."


def build_messages(topic: str, difficulty: str, history: list[Message], question: Question | None = None) -> list[Message]:
    """System prompt + kickoff + the most recent history. The kickoff keeps the first non-system role 'user'.
    With a bank question, the model writes a follow-up about it; without one it falls back to the generic coach prompt."""
    recent = history[-MAX_HISTORY_MESSAGES:]
    while recent and recent[0].role != "assistant":
        recent = recent[1:]  # never start mid-exchange
    if question is None:
        system = _SYSTEM.format(topic=topic, difficulty=difficulty)
    else:
        system = _FOLLOWUP_SYSTEM.format(
            topic=topic, difficulty=difficulty, question=question.question, points="; ".join(question.key_points)
        )
    return [Message("system", system), Message("user", _KICKOFF), *recent]


def _asked_block(asked: list[Question]) -> str:
    lines = [f"{i}. {q.question} Key points: {'; '.join(q.key_points)}" for i, q in enumerate(asked, start=1)]
    return (
        " The questions asked, with the key points a strong answer covers:\n" + "\n".join(lines) + "\n"
        "Use the key points only to judge which ones the candidate actually covered; do not credit points that were not said."
    )


def build_summary_messages(topic: str, difficulty: str, history: list[Message], asked: list[Question] | None = None) -> list[Message]:
    """Summaries see the whole conversation (bounded already by the 60 s per-utterance cap and session length), then a final
    user turn with the request. The history of an ended interview ends with the coach's message, so roles still alternate.
    Topic and difficulty are in the system prompt so the feedback is judged against the right level."""
    system = _SUMMARY_SYSTEM.format(tag=SUMMARY_TAG, topic=topic, difficulty=difficulty)
    if asked:
        system += _asked_block(asked)
    return [Message("system", system), Message("user", _KICKOFF), *history, Message("user", _SUMMARY_REQUEST)]
