"""Delivery feedback from measurable signals only: speaking rate, filler words, pauses, answer length. Pure: no FastAPI,
no numpy, no ML. Nothing here judges tone or emotion.

Pauses come from the audio itself (frame loudness), not from the ASR, so they work with any provider, including the fake.
The thresholds are starting guesses, not tuned against real speech.
"""
import math
import re
import sys
from array import array
from dataclasses import dataclass

FRAME_S = 0.03  # 30 ms frames
PAUSE_MIN_S = 1.0  # a silent gap at least this long inside an answer counts as a pause
MIN_RMS = 200.0  # on the PCM16 scale (max 32767): quieter than this is always silence (about -44 dBFS)
RELATIVE_SILENCE = 0.1  # a frame is silent below 10% of the clip's loud level, so a quiet microphone still works
MIN_SPEAKING_S = 1.0  # below this, words per minute is too noisy to report

# Conservative on purpose: only clear disfluencies. "like", "so", "actually" are often real words ("I like lists").
_FILLERS = re.compile(r"\b(?:u+h+m*|u+m+|e+r+m*|h+m+|a+h+|you know|i mean)\b", re.IGNORECASE)
_WORDS = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class DeliveryStats:
    words: int
    answer_s: float  # whole clip, as recorded
    speaking_s: float  # first to last speech frame: leading and trailing silence removed
    wpm: float | None  # None when there was too little speech to measure
    fillers: int
    pauses: int  # silent gaps of at least PAUSE_MIN_S between speech
    longest_pause_s: float


def _frame_rms(pcm: bytes, sample_rate: int) -> list[float]:
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()  # PCM16 from the browser is little-endian
    size = max(1, round(sample_rate * FRAME_S))
    return [
        math.sqrt(sum(s * s for s in samples[i : i + size]) / len(samples[i : i + size]))
        for i in range(0, len(samples) - size + 1, size)  # a partial last frame is dropped: under 30 ms
    ]


def speech_frames(pcm: bytes, sample_rate: int) -> list[bool]:
    """True for each 30 ms frame that is speech. Threshold: max(MIN_RMS, 10% of the clip's 90th-percentile loudness)."""
    rms = _frame_rms(pcm, sample_rate)
    if not rms:
        return []
    loud = sorted(rms)[int(0.9 * (len(rms) - 1))]
    threshold = max(MIN_RMS, RELATIVE_SILENCE * loud)
    return [value >= threshold for value in rms]


def count_fillers(text: str) -> int:
    return len(_FILLERS.findall(text))


def analyze(text: str, pcm: bytes, sample_rate: int) -> DeliveryStats:
    frames = speech_frames(pcm, sample_rate)
    words = len(_WORDS.findall(text))
    answer_s = len(pcm) / 2 / sample_rate
    spoken = [i for i, is_speech in enumerate(frames) if is_speech]
    if not spoken:
        return DeliveryStats(words, round(answer_s, 2), 0.0, None, count_fillers(text), 0, 0.0)
    first, last = spoken[0], spoken[-1]
    speaking_s = (last - first + 1) * FRAME_S
    gaps, run = [], 0
    for is_speech in frames[first : last + 1]:
        if is_speech:
            if run:
                gaps.append(run * FRAME_S)
            run = 0
        else:
            run += 1
    pauses = [g for g in gaps if g >= PAUSE_MIN_S - 1e-9]
    wpm = round(words / speaking_s * 60, 1) if speaking_s >= MIN_SPEAKING_S else None
    return DeliveryStats(
        words, round(answer_s, 2), round(speaking_s, 2), wpm, count_fillers(text), len(pauses), round(max(pauses, default=0.0), 2)
    )


def session_line(stats: list[DeliveryStats]) -> str | None:
    """One plain sentence with the session's numbers, written by code so no number is ever invented. None without answers."""
    if not stats:
        return None
    measured = [s for s in stats if s.wpm is not None]
    words = sum(s.words for s in measured)
    speaking = sum(s.speaking_s for s in measured)
    rate = f"{round(words / speaking * 60)} words per minute" if speaking >= MIN_SPEAKING_S else "speaking rate not measurable"
    average = sum(s.answer_s for s in stats) / len(stats)
    fillers = sum(s.fillers for s in stats)
    pauses = sum(s.pauses for s in stats)
    longest = max(s.longest_pause_s for s in stats)
    answers = f"{len(stats)} answer{'s' if len(stats) != 1 else ''}"
    pause_text = f"{pauses} pause{'s' if pauses != 1 else ''} of {PAUSE_MIN_S:g} s or more"
    if pauses:
        pause_text += f" (longest {longest:.1f} s)"
    return (
        f"{answers}, {average:.0f} s on average; {rate}; {fillers} filler word{'s' if fillers != 1 else ''} "
        f"(um, uh, er, you know, I mean); {pause_text}."
    )
