from backend.sentences import SentenceSplitter


def run(pieces: list[str], **kwargs) -> list[str]:
    splitter = SentenceSplitter(**kwargs)
    out: list[str] = []
    for piece in pieces:
        out += splitter.feed(piece)
    return out + splitter.flush()


def test_splits_on_sentence_ends():
    assert run(["Hello there. How are you? Fine!"]) == ["Hello there.", "How are you?", "Fine!"]


def test_sentence_is_emitted_as_soon_as_the_next_piece_confirms_it():
    splitter = SentenceSplitter()
    assert splitter.feed("First one.") == []  # the "." might be part of "3.5": wait
    assert splitter.feed(" Sec") == ["First one."]
    assert splitter.feed("ond.") == []
    assert splitter.flush() == ["Second."]


def test_pieces_split_mid_word_give_the_same_result_as_one_piece():
    text = "Explain a list. Then a tuple? Done."
    assert run(list(text)) == run([text])


def test_decimals_and_abbreviations_do_not_split():
    assert run(["Python 3.5 is old, e.g. for this. Next."]) == ["Python 3.5 is old, e.g. for this.", "Next."]
    assert run(["Ask Dr. Rao about it."]) == ["Ask Dr. Rao about it."]


def test_closing_quote_stays_with_its_sentence():
    assert run(['He said "stop." Then left.']) == ['He said "stop."', "Then left."]


def test_flush_returns_unterminated_tail_once():
    splitter = SentenceSplitter()
    splitter.feed("No ending here")
    assert splitter.flush() == ["No ending here"]
    assert splitter.flush() == []


def test_long_text_without_punctuation_is_force_split_on_a_space():
    out = run(["word " * 10], max_chars=20)
    assert all(len(s) <= 20 for s in out)
    assert " ".join(out) == ("word " * 10).strip()


def test_one_giant_word_is_hard_cut():
    assert run(["x" * 25], max_chars=10) == ["x" * 10, "x" * 10, "x" * 5]


def test_empty_and_whitespace_pieces_yield_nothing():
    assert run(["", "  ", "\n"]) == []
