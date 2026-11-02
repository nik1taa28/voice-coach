"""Print one compact table from loadtest/results: Locust's CSV (client view) next to the server's own /metrics (server view).

    python loadtest/summarize.py

Client percentiles come from Locust's CSV and are approximated by Locust (rounded buckets). Server percentiles are exact over
the server's window. The two clocks are different views of the same requests: compare them, do not subtract them.
"""
import csv
import json
import re
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
ANSWER = "answer -> coach_done"


def client_rows(stats_csv: Path) -> tuple[dict, dict]:
    with stats_csv.open(newline="") as handle:
        rows = {row["Name"]: row for row in csv.DictReader(handle)}
    for name in (ANSWER, "Aggregated"):
        if name not in rows:
            raise SystemExit(f"{stats_csv.name}: no row named {name!r}; found {sorted(rows)}")
    return rows[ANSWER], rows["Aggregated"]


def fmt(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}" if value < 10 else f"{value:.0f}"


def main() -> None:
    def order(label: str) -> tuple:  # u100r2 -> ("r", 100, 2); u8w -> ("w", 8, 0): groups by suffix letter, then users, then run
        users, kind, run = re.match(r"u(\d+)([a-z]?)(\d*)", label).groups()
        return (kind, int(users), int(run or 0))

    labels = sorted({m.group(1) for p in RESULTS.glob("u*_stats.csv") if (m := re.match(r"(u\d+(?:[a-z]\d*)?)_stats\.csv", p.name))},
                    key=order)
    if not labels:
        raise SystemExit(f"no u*_stats.csv in {RESULTS}")
    header = ("users", "answers", "fails", "all req/s", "client p50", "client p95", "srv asr p50", "srv asr p95", "srv llm_total p50",
              "srv llm_total p95", "srv tts p95", "error rate")
    print(" | ".join(header))
    for label in labels:
        answer, aggregated = client_rows(RESULTS / f"{label}_stats.csv")
        metrics_files = sorted(RESULTS.glob(f"metrics-{label}-*.json"))
        if not metrics_files:
            print(f"{label}: no metrics file")
            continue
        server = json.loads(metrics_files[-1].read_text())
        stages = server["stages"]
        rate = server["error_rate"]
        print(" | ".join([
            label[1:] + (" HUNG" if (RESULTS / f"{label}.HUNG").exists() else ""), answer["Request Count"], aggregated["Failure Count"], aggregated["Requests/s"][:5],
            answer["50%"], answer["95%"],
            fmt(stages["asr"]["p50"]), fmt(stages["asr"]["p95"]), fmt(stages["llm_total"]["p50"]), fmt(stages["llm_total"]["p95"]), fmt(stages["tts"]["p95"]),
            "–" if rate is None else f"{rate:.3f}",
        ]))
    print("\nclient = Locust (answer -> coach_done, ms); srv = server /metrics (ms), over the server's METRICS_WINDOW (see each metrics file)")


if __name__ == "__main__":
    main()
