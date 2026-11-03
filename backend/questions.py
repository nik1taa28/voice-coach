"""Curated question bank: pure loading, validation and picking. No FastAPI, no network, so it is unit testable."""
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path

DIFFICULTIES = ("easy", "medium", "hard")
DEFAULT_BANK_PATH = Path(__file__).resolve().parent.parent / "data" / "question_bank.json"  # data, not code: lives outside backend/
DEFAULT_FOLLOWUPS = 2
MAX_FOLLOWUPS = 5


class QuestionBankError(ValueError):
    """The bank file is missing or malformed. Raised at startup so a bad file never reaches a live interview."""


@dataclass(frozen=True)
class Question:
    id: str
    topic: str
    difficulty: str
    question: str
    key_points: tuple[str, ...]


def _parse(index: int, raw: object) -> Question:
    where = f"entry {index}"
    if not isinstance(raw, dict):
        raise QuestionBankError(f"{where}: expected an object")
    for field in ("id", "topic", "difficulty", "question"):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise QuestionBankError(f"{where}: '{field}' must be a non-empty string")
    where = f"{raw['id']}"
    if raw["difficulty"] not in DIFFICULTIES:
        raise QuestionBankError(f"{where}: difficulty must be one of {', '.join(DIFFICULTIES)}")
    points = raw.get("key_points")
    if not isinstance(points, list) or not points or not all(isinstance(p, str) and p.strip() for p in points):
        raise QuestionBankError(f"{where}: 'key_points' must be a non-empty list of non-empty strings")
    return Question(raw["id"], raw["topic"].strip(), raw["difficulty"], raw["question"].strip(), tuple(p.strip() for p in points))


def load_bank(path: Path = DEFAULT_BANK_PATH) -> list[Question]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuestionBankError(f"cannot read question bank {path}: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise QuestionBankError("question bank must be a non-empty JSON list")
    bank = [_parse(i, raw) for i, raw in enumerate(data)]
    ids = [q.id for q in bank]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise QuestionBankError(f"duplicate question ids: {', '.join(duplicates)}")
    return bank


def followups_per_question() -> int:
    """LLM follow-ups asked after each bank question, from FOLLOWUPS_PER_QUESTION (default 2). A bad value fails loudly."""
    raw = os.environ.get("FOLLOWUPS_PER_QUESTION", str(DEFAULT_FOLLOWUPS))
    try:
        value = int(raw)
    except ValueError:
        raise QuestionBankError(f"FOLLOWUPS_PER_QUESTION must be an integer, got {raw!r}") from None
    if not 0 <= value <= MAX_FOLLOWUPS:
        raise QuestionBankError(f"FOLLOWUPS_PER_QUESTION must be between 0 and {MAX_FOLLOWUPS}, got {value}")
    return value


def topics(bank: list[Question]) -> list[str]:
    """Topic names in first-seen order (what the UI dropdown shows)."""
    return list(dict.fromkeys(q.topic for q in bank))


def levels_by_distance(difficulty: str) -> list[str]:
    """The chosen level first, then the others from nearest to farthest; ties go to the easier one (medium -> easy, hard)."""
    chosen = DIFFICULTIES.index(difficulty)
    return sorted(DIFFICULTIES, key=lambda level: (abs(DIFFICULTIES.index(level) - chosen), DIFFICULTIES.index(level)))


def pick_question(bank: list[Question], topic: str, difficulty: str, asked: set[str], rng: random.Random | None = None) -> Question | None:
    """A random unasked question on this topic, from the chosen level if any is left, otherwise from the nearest other
    level (the bank has only 2-6 questions per topic and level, fewer than most interviews need). The question keeps its
    own difficulty, so callers judge it against that. None only when the whole topic is used up."""
    for level in levels_by_distance(difficulty):
        candidates = [q for q in bank if q.topic == topic and q.difficulty == level and q.id not in asked]
        if candidates:
            return (rng or random).choice(candidates)
    return None
