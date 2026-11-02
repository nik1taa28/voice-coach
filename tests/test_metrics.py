import json

import pytest

from backend.metrics import Metrics, percentile


def test_percentile_nearest_rank_known_values():
    data = [float(i) for i in range(1, 101)]  # 1..100
    assert percentile(data, 50) == 50.0
    assert percentile(data, 95) == 95.0
    assert percentile(data, 100) == 100.0


def test_percentile_small_samples():
    assert percentile([7.0], 50) == 7.0
    assert percentile([7.0], 95) == 7.0
    assert percentile([1.0, 2.0], 50) == 1.0  # ceil(0.5 * 2) = rank 1
    assert percentile([1.0, 2.0], 95) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0  # ceil(3.8) = rank 4


def test_percentile_rejects_empty_and_bad_q():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 0)
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_empty_snapshot_has_null_not_zero():
    snap = Metrics().snapshot()
    assert snap["stages"]["asr"] == {"n": 0, "p50": None, "p95": None}
    assert snap["error_rate"] is None
    json.dumps(snap)  # must be serialisable


def test_observe_and_snapshot_sorts_unordered_input():
    m = Metrics()
    for ms in (30.0, 10.0, 20.0):
        m.observe("asr", ms)
    assert m.snapshot()["stages"]["asr"] == {"n": 3, "p50": 20.0, "p95": 30.0}


def test_window_is_bounded_and_keeps_the_newest():
    m = Metrics(window=100)
    for i in range(10_000):
        m.observe("llm_ttft", float(i))
    stage = m.snapshot()["stages"]["llm_ttft"]
    assert stage["n"] == 100
    assert stage["p50"] == 9949.0  # newest 100 are 9900..9999; rank 50 -> 9949


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_bad_samples_are_dropped_and_counted(bad):
    m = Metrics()
    m.observe("asr", bad)
    snap = m.snapshot()
    assert snap["stages"]["asr"]["n"] == 0
    assert snap["counts"]["rejected_samples"] == 1


def test_from_env_default_and_custom_window():
    assert Metrics.from_env({}).snapshot()["window"] == 1000
    assert Metrics.from_env({"METRICS_WINDOW": "50000"}).snapshot()["window"] == 50000


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "1.5", ""])
def test_from_env_rejects_bad_window(bad):
    with pytest.raises(ValueError, match="METRICS_WINDOW"):
        Metrics.from_env({"METRICS_WINDOW": bad})


def test_unknown_names_raise():
    m = Metrics()
    with pytest.raises(ValueError):
        m.observe("asrr", 1.0)
    with pytest.raises(ValueError):
        m.count("turn_okk")


def test_error_rate_ignores_cancelled_and_speech_failures():
    m = Metrics()
    for _ in range(6):
        m.count("turn_ok")
    m.count("turn_failed")
    m.count("turn_timeout")
    for _ in range(5):
        m.count("turn_cancelled")
    m.count("speech_failed")
    assert m.snapshot()["error_rate"] == pytest.approx(2 / 8)


def test_only_cancelled_turns_gives_null_error_rate():
    m = Metrics()
    m.count("turn_cancelled")
    assert m.snapshot()["error_rate"] is None
