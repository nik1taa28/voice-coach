import json
import random

import pytest

from backend.questions import QuestionBankError, load_bank, pick_question, topics
from backend.session_state import DIFFICULTIES


def write_bank(tmp_path, entries):
    path = tmp_path / "bank.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def entry(**overrides):
    base = {"id": "t-1", "topic": "T", "difficulty": "easy", "question": "Q?", "key_points": ["a"]}
    return {**base, **overrides}


def test_shipped_bank_loads_and_covers_every_topic_and_level():
    bank = load_bank()
    for topic in topics(bank):
        for difficulty in DIFFICULTIES:
            assert any(q.topic == topic and q.difficulty == difficulty for q in bank), (topic, difficulty)


def test_shipped_bank_has_at_least_two_questions_per_cell_so_a_session_can_continue():
    bank = load_bank()
    for topic in topics(bank):
        for difficulty in DIFFICULTIES:
            assert sum(q.topic == topic and q.difficulty == difficulty for q in bank) >= 2


def test_topics_keep_first_seen_order_without_duplicates(tmp_path):
    bank = load_bank(write_bank(tmp_path, [entry(id="a", topic="B"), entry(id="b", topic="A"), entry(id="c", topic="B")]))
    assert topics(bank) == ["B", "A"]


@pytest.mark.parametrize("bad", [
    entry(difficulty="impossible"),
    entry(key_points=[]),
    entry(key_points=["ok", " "]),
    entry(question=" "),
    entry(topic=None),
])
def test_invalid_entries_are_rejected(tmp_path, bad):
    with pytest.raises(QuestionBankError):
        load_bank(write_bank(tmp_path, [bad]))


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(QuestionBankError, match="duplicate"):
        load_bank(write_bank(tmp_path, [entry(), entry()]))


def test_missing_file_and_bad_json_give_a_clear_error(tmp_path):
    with pytest.raises(QuestionBankError, match="cannot read"):
        load_bank(tmp_path / "nope.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(QuestionBankError, match="cannot read"):
        load_bank(broken)


def test_empty_bank_is_rejected(tmp_path):
    with pytest.raises(QuestionBankError):
        load_bank(write_bank(tmp_path, []))


def test_pick_matches_topic_and_level_and_skips_asked():
    bank = load_bank()
    first = pick_question(bank, "Python basics", "easy", set(), random.Random(1))
    assert (first.topic, first.difficulty) == ("Python basics", "easy")
    second = pick_question(bank, "Python basics", "easy", {first.id}, random.Random(1))
    assert second.id != first.id


def test_pick_returns_none_when_everything_was_asked_or_topic_unknown():
    bank = load_bank()
    every_id = {q.id for q in bank}
    assert pick_question(bank, "Python basics", "easy", every_id) is None
    assert pick_question(bank, "Underwater basket weaving", "easy", set()) is None


def test_pick_is_deterministic_for_a_seeded_rng():
    bank = load_bank()
    a = pick_question(bank, "Data structures", "hard", set(), random.Random(7))
    b = pick_question(bank, "Data structures", "hard", set(), random.Random(7))
    assert a == b


# --- follow-ups per question setting ---

from backend.questions import followups_per_question


def test_followups_default_is_two(monkeypatch):
    monkeypatch.delenv("FOLLOWUPS_PER_QUESTION", raising=False)
    assert followups_per_question() == 2


@pytest.mark.parametrize("raw,expected", [("0", 0), ("1", 1), ("5", 5)])
def test_followups_can_be_set(monkeypatch, raw, expected):
    monkeypatch.setenv("FOLLOWUPS_PER_QUESTION", raw)
    assert followups_per_question() == expected


@pytest.mark.parametrize("raw", ["-1", "6", "two", "", "1.5"])
def test_followups_bad_values_fail_loudly(monkeypatch, raw):
    monkeypatch.setenv("FOLLOWUPS_PER_QUESTION", raw)
    with pytest.raises(QuestionBankError):
        followups_per_question()
