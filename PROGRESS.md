# Progress

## Summary (read this first)

**Read first:** `docs/PROJECT_BRIEF.md` (sections 2, 6 and 8), then `docs/performance.md`.

| | Status |
|---|---|
| M0 skeleton | Done, committed (`de0fd7a`), tests pass |
| M1 mic -> ASR | Done, committed (`adc8169`), tests pass; real Whisper verified by hand |
| M2 LLM + state machine | Done, committed (`360bfd0`), tests pass; real Gemini verified once by hand |
| M3 TTS + pipelining | Done, committed (`df16505`), tests pass; verified by hand in one browser |
| M4 reliability | Done, committed (`74ce23c`), tests pass; verified by hand on real uvicorn (fake providers) |
| M5 observability | **Done in the working tree, NOT committed (waiting for your OK).** Metrics, `/metrics`, JSON logs with session ids, dashboard, Locust load test, baseline report, learning notes |
| M6 Docker / CI / README numbers | Not started |
| Curated question bank | Planned, after M6 (your decision). Not built |

**Tests:** `186 passed in 3.50s` (`.venv/bin/python -m pytest -q`), run by Claude on 2026-11-01. 148 are M0-M4; 38 are M5.

**Failed / open:** nothing is failing. The one failure, the Locust 100-user hang, is **fixed** (see below).

**Needs you:**
1. **Commit M5?** Nothing was committed. `git status` shows the M5 files plus your two `CLAUDE.md` lines and an edited README/architecture/learning-notes.
2. **What to do with `loadtest/results/`:** it is 52 MB, mostly 4 MB server logs and archived runs. Suggest ignoring it (or only keeping the CSV/JSON summaries) before committing.
3. **Your own tests:** real microphone, real Whisper + Gemini end to end, other browsers (commands in `docs/PROJECT_BRIEF.md` section 7).
4. A server from your terminal is still listening on port 8000 (PID 56194). I never touched it.

## What this session added (M5)

- `backend/metrics.py`, `GET /metrics`, `METRICS_WINDOW` env (default 1000): 18 + 8 tests.
- `backend/logs.py`: one JSON object per line, `session_id` from a contextvar: 8 tests; seen on real uvicorn.
- `frontend/dashboard.html`, `GET /dashboard`: 4 tests; dark mode and the "cannot read /metrics" banner seen by hand. The `asr` row filling on screen after a spoken answer was not confirmed visually (the server side was confirmed by the load test).
- `loadtest/` (`locustfile.py`, `sweep.sh`, `warmup.py`, `summarize.py`, `audio/answer.wav`), `requirements-dev.txt`.
- `docs/performance.md` (measured numbers only), M5 section in `docs/learning-notes.md`, rows in `docs/architecture.md`, `docs/PROJECT_BRIEF.md`.

## The Locust 100-user hang: fixed

- **Cause:** `websocket-client`'s `close()` ends in a bare `except: pass`, which swallows the `GreenletExit` Locust uses to stop a user. A user killed inside `close()` kept running and Locust waited for it forever. Reproduced in isolation.
- **Fix:** `ws.shutdown()` in the load-test script (one line plus a comment).
- **Evidence:** before, 2 of 2 runs at 100 users hung. After, 24 of 24 runs exited by themselves, including six 100-user runs. Good evidence for a probabilistic failure, not proof. `sweep.sh` now has a watchdog that records a `HUNG` marker and stops a stuck Locust, so a recurrence is detected.
- It was a bug in my test script, not in the app.

## Results in one paragraph (details and caveats in `docs/performance.md`)

Fake providers, 1-100 users, 3 repeats: 0 failures, throughput about linear to 336 req/s, no saturation found. Real Whisper (`tiny.en`) with fake LLM/TTS, 1-32 users: 0 failures and no timeouts, but throughput plateaus at about 4 answers/s and asr p50 grows 242 ms (1 user), 674 ms (8), 2868 ms (16), 7167 ms (32). Other stages stay small, so the limit is the Whisper stage. Not explained: why the plateau sits there (a guess: one transcription at a time). Caveats: load generator on the same laptop, synthetic `say` voice, closed-loop users, low-user numbers are inflated by an idle-clocked CPU (shown with a burner experiment on the fake runs only).

## Not done / pending

- Real Gemini under load (not planned: free-tier limits).
- The whole real pipeline (Whisper + Gemini + voice) under load, or even once end to end.
- Testing the Whisper plateau guess (change `num_workers` / `cpu_threads`, rerun 8-16 users).
- Open-loop load, separate load-generator machine, memory/CPU of the server, runs longer than 60 s.
- Timeout tuning (M4 deadlines unchanged; the 30 s ASR deadline never fired in the Whisper test).
- M6: Docker, GitHub Actions CI, README with verified numbers.
- Question bank (planned after M6) and the delivery-feedback stretch goal.

## Housekeeping

- `CLAUDE.md` has two uncommitted lines you added (question bank, delivery-feedback stretch). Left as is.
- `loadtest/results/` subfolders: `archive-1000-window/` (runs with the old 1000-sample window, valid), `invalid-u100-*` (the two hung runs), `experiment-cpu-burners/`, `smoke/`. `summarize.py` reads only the top-level files.
