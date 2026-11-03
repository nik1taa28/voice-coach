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
| M5 observability | Done, committed (`6e1787a`, branch `m5-observability`). Metrics, `/metrics`, JSON logs with session ids, dashboard, Locust load test, baseline report, learning notes |
| M6 Docker / CI / README numbers | **Written, partly verified.** README updated with measured numbers; CI recipe verified in a clean venv (186 passed); demo mode verified from a clean venv. **Dockerfile not built** (no Docker daemon) and **CI not run on GitHub** |
| Curated question bank | **Built, uncommitted.** 65 questions, 7 topics (OS split into 3). Bank question spoken with no LLM call, then 2 LLM follow-ups (`FOLLOWUPS_PER_QUESTION`), `GET /topics`, topic dropdown, key points in the prompts. Tests and a demo-mode server run only; **real Gemini run pending** |

**Tests:** `236 passed in 3.63s` (`.venv/bin/python -m pytest -q`), run by Claude on 2026-11-03. About 25 older WebSocket tests were rewritten because the opener is now a bank question.

**Failed / open:** the Locust 100-user hang is fixed (see below). **Found in your real runs: the end summary was wrong** (Gemini asked another question instead of summarising). Fixed in `backend/prompts.py` (final user turn; four parts: Summary, Strengths, Areas to improve, Practice next; topic and difficulty now passed in) and tested with the fake only; **press End in a real Gemini run to confirm**.

**Needs you:**
0. **Review `backend/question_bank.json`** (65 questions I wrote; difficulty labels are my judgement) and **run the real pipeline once**: `source .env`, start, answer 2-3 times, End. Check the summary has the four parts, mentions topic/level, a concrete *Practice next*, no question; and that follow-ups do not read the key points out. Gemini free tier was 20 requests/day on `gemini-2.5-flash` (429 seen on 2026-11-02); bank turns now save calls.
1. **Build and run the Docker image** (commands in `docs/PROJECT_BRIEF.md` section 7, step I). Start Docker Desktop first.
2. **Push a branch to GitHub** to get the first real CI run (I did not push anything).
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
