"""In-memory latency and outcome metrics. Pure Python: no FastAPI, so every rule is a plain unit test.

Per-process and not persisted: a restart resets everything, and with several uvicorn workers each would have its own numbers.
The event loop is single-threaded and observe/count never await, so no lock is needed.
"""
import math
import os
from collections import deque
from typing import Mapping

STAGES = ("asr", "llm_ttft", "llm_total", "first_sentence", "first_speak", "tts")
COUNTERS = ("turn_ok", "turn_failed", "turn_timeout", "turn_cancelled", "speech_failed", "decision_fallback", "rejected_samples")
WINDOW = 1000  # samples kept per stage; percentiles describe the most recent WINDOW, not all time


def percentile(sorted_samples: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest sample with at least q% of the samples at or below it.

    No interpolation, so the result is always a value that really was observed. `sorted_samples` must be sorted ascending.
    """
    if not sorted_samples:
        raise ValueError("percentile of no samples")
    if not 0 < q <= 100:
        raise ValueError(f"q must be in (0, 100], got {q}")
    rank = math.ceil(q / 100 * len(sorted_samples))
    return sorted_samples[rank - 1]


class Metrics:
    def __init__(self, window: int = WINDOW) -> None:
        self._samples = {stage: deque(maxlen=window) for stage in STAGES}
        self._counts = {name: 0 for name in COUNTERS}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Metrics":
        """METRICS_WINDOW = samples kept per stage (default 1000). A load test raises it so the percentiles cover the whole run
        instead of only its last seconds. A bad value fails loudly, like the TIMEOUT_* variables."""
        env = os.environ if env is None else env
        raw = env.get("METRICS_WINDOW")
        if raw is None:
            return cls()
        try:
            window = int(raw)
        except ValueError:
            raise ValueError(f"METRICS_WINDOW must be a whole number, got {raw!r}") from None
        if window < 1:
            raise ValueError(f"METRICS_WINDOW must be at least 1, got {raw!r}")
        return cls(window)

    def observe(self, stage: str, ms: float) -> None:
        """Record one duration. An unknown stage is a bug (typo) and raises. A bad value (nan, inf, negative) is dropped and
        counted in `rejected_samples`, so a measuring problem never breaks a turn and never hides."""
        if stage not in self._samples:
            raise ValueError(f"unknown stage {stage!r}")
        if not math.isfinite(ms) or ms < 0:
            self._counts["rejected_samples"] += 1
            return
        self._samples[stage].append(ms)

    def count(self, name: str) -> None:
        if name not in self._counts:
            raise ValueError(f"unknown counter {name!r}")
        self._counts[name] += 1

    def snapshot(self) -> dict:
        """JSON-safe view. A stage with no samples reports null percentiles, never a fake 0.

        error_rate = (failed + timeout) / (ok + failed + timeout), or null before any turn finished. Cancelled turns are
        the user's choice (Pause / End), so they are not errors and not in the denominator. `speech_failed` is separate: the
        text still arrived.
        """
        stages = {}
        for stage, samples in self._samples.items():
            ordered = sorted(samples)
            stages[stage] = {
                "n": len(ordered),
                "p50": percentile(ordered, 50) if ordered else None,
                "p95": percentile(ordered, 95) if ordered else None,
            }
        counts = dict(self._counts)
        errors = counts["turn_failed"] + counts["turn_timeout"]
        finished = counts["turn_ok"] + errors
        return {
            "window": next(iter(self._samples.values())).maxlen,
            "stages": stages,
            "counts": counts,
            "error_rate": errors / finished if finished else None,
        }
