import math
import time
from array import array

import pytest

from backend.audio import SAMPLE_RATE
from backend.delivery import DeliveryStats, analyze, count_fillers, session_line, speech_frames


def tone(seconds: float, amplitude: int = 8000) -> bytes:
    """A 220 Hz sine: stands in for speech. Little-endian PCM16, like the browser sends."""
    n = round(seconds * SAMPLE_RATE)
    samples = array("h", (round(amplitude * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE)) for i in range(n)))
    return samples.tobytes()


def silence(seconds: float, noise: int = 0) -> bytes:
    n = round(seconds * SAMPLE_RATE)
    return array("h", ((noise if i % 2 else -noise) for i in range(n))).tobytes()


WORDS_20 = " ".join(["word"] * 20)


def test_a_long_gap_inside_the_answer_is_a_pause():
    stats = analyze(WORDS_20, tone(1) + silence(1.5) + tone(1), SAMPLE_RATE)
    assert stats.pauses == 1 and stats.longest_pause_s == pytest.approx(1.5, abs=0.06)
    assert stats.speaking_s == pytest.approx(3.5, abs=0.06)


def test_leading_and_trailing_silence_is_neither_a_pause_nor_speaking_time():
    stats = analyze(WORDS_20, silence(2) + tone(2) + silence(3), SAMPLE_RATE)
    assert stats.pauses == 0
    assert stats.speaking_s == pytest.approx(2, abs=0.06) and stats.answer_s == pytest.approx(7)


def test_short_gaps_are_not_pauses():
    stats = analyze(WORDS_20, tone(1) + silence(0.6) + tone(1) + silence(0.9) + tone(1), SAMPLE_RATE)
    assert stats.pauses == 0 and stats.longest_pause_s == 0.0


def test_words_per_minute_uses_speaking_time():
    stats = analyze(WORDS_20, silence(1) + tone(10) + silence(1), SAMPLE_RATE)
    assert stats.wpm == pytest.approx(120, abs=1)  # 20 words in 10 s of speech, not in the 12 s clip


def test_all_silence_has_no_rate_and_no_pauses():
    stats = analyze("fake transcript", silence(3), SAMPLE_RATE)
    assert (stats.speaking_s, stats.wpm, stats.pauses) == (0.0, None, 0)


def test_too_little_speech_reports_no_rate():
    assert analyze("yes", tone(0.5), SAMPLE_RATE).wpm is None


def test_a_quiet_microphone_is_still_detected_and_its_noise_floor_is_silence():
    frames = speech_frames(tone(1, amplitude=900) + silence(1, noise=60) + tone(1, amplitude=900), SAMPLE_RATE)
    third = len(frames) // 3
    assert all(frames[:third - 1]) and not any(frames[third + 1 : 2 * third - 1]) and all(frames[2 * third + 1 :])


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Um, so I think, uh, you know, it's a list.", 3),
        ("I like lists, I mean they are mutable. Ermm, hmm.", 3),
        ("Umbrella, error, human, ahead, unknown, meaning.", 0),  # word boundaries: no fillers inside real words
        ("I like Python, so basically it is actually fine.", 0),  # ambiguous words are not counted
        ("", 0),
    ],
)
def test_fillers_are_counted_conservatively(text, expected):
    assert count_fillers(text) == expected


def test_session_line_is_written_from_the_numbers():
    stats = [
        DeliveryStats(words=40, answer_s=22, speaking_s=20, wpm=120, fillers=2, pauses=1, longest_pause_s=1.8),
        DeliveryStats(words=10, answer_s=8, speaking_s=5, wpm=120, fillers=1, pauses=0, longest_pause_s=0),
    ]
    assert session_line(stats) == (
        "2 answers, 15 s on average; 120 words per minute; 3 filler words (um, uh, er, you know, I mean); "
        "1 pause of 1 s or more (longest 1.8 s)."
    )


def test_session_line_without_measurable_speech_says_so():
    stats = [DeliveryStats(words=3, answer_s=1, speaking_s=0.0, wpm=None, fillers=0, pauses=0, longest_pause_s=0.0)]
    assert session_line(stats) == (
        "1 answer, 1 s on average; speaking rate not measurable; 0 filler words (um, uh, er, you know, I mean); "
        "0 pauses of 1 s or more."
    )
    assert session_line([]) is None


def test_a_60_second_answer_is_analysed_quickly_enough_to_run_in_a_thread():
    pcm = tone(60)
    started = time.perf_counter()
    analyze(WORDS_20, pcm, SAMPLE_RATE)
    elapsed = time.perf_counter() - started
    print(f"analyze() on 60 s of audio: {elapsed * 1000:.0f} ms")
    assert elapsed < 2.0  # loose bound so slow CI machines pass; the printed number is the measurement
