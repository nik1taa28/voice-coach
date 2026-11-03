# Progress

## Summary (read this first)

**Read first:** `docs/PROJECT_BRIEF.md` (sections 2, 6 and 8), then `docs/performance.md`. Current branch: `question-loop` (built on `m6-docker-ci`); nothing is merged to `main` or pushed.

| | Status |
|---|---|
| M0 skeleton | Done, committed (`de0fd7a`), tests pass |
| M1 mic -> ASR | Done, committed (`adc8169`), tests pass; real Whisper verified by hand |
| M2 LLM + state machine | Done, committed (`360bfd0`), tests pass; real Gemini verified once by hand |
| M3 TTS + pipelining | Done, committed (`df16505`), tests pass; verified by hand in one browser |
| M4 reliability | Done, committed (`74ce23c`), tests pass; verified by hand on real uvicorn (fake providers) |
| M5 observability | Done, committed (`6e1787a`). Metrics, `/metrics`, JSON logs, dashboard, Locust load test, baseline report |
| M6 Docker / CI / README numbers | **Written, partly verified.** CI recipe verified in a clean venv; demo mode from a clean venv. **Docker image built and run locally on 2026-11-03** (demo mode: 25 s build, 251 MB, healthy, full interview through the container; the `WITH_ASR=1` image is not built) and **CI run on GitHub once (user-reported, 2026-11-03): a green tick on the Actions page for the first push of `question-loop`**. Repo: https://github.com/nik1taa28/voice-coach (`main` = M0 only; `question-loop` has everything, pull request opened by the user). Which jobs ran, durations and the run number were not reported |
| Curated question bank | Done (`a14f113`, moved to `data/` in `2f91d68`). 65 questions, 7 topics. Summary prompt fixed after two real Gemini runs (`bb79da9`) |
| Code-owned loop | Done (`0bb9a1b`..`1c54177`). JSON follow_up / move_on with fallback, follow-up cap in code, automatic end after 5/6/8/10 questions, at the time limit or when the bank runs out, `ending` state. Borrowing from the nearest level (`805f13a`). One real Gemini session: 2 valid decisions, 1 invalid-reply fallback (**cause pending**) |
| Delivery feedback (stretch) | Done (`2d4b84c`, `6f09676`, docs). Words per minute, fillers, pauses, length; measured by code, never by the LLM. **Real microphone not validated** |

**Tests:** `307 passed in 4.05s` (`.venv/bin/python -m pytest -q`), run by Claude on 2026-11-03.

**Needs you (in order of value):**
1. **The `invalid reply` log line** from the real Gemini run (search your server terminal). It says why one decision fell back.
2. **One real session** with the new build: Python basics, 6 questions. It should reach 6 by borrowing levels. Check the delivery line under the summary with your real microphone, and the page in a browser.
3. ~~Docker~~ done in demo mode (see `docs/architecture.md`). Optional: the `WITH_ASR=1` image.
4. ~~CI~~ pushed by the user and green. Remaining: merge the pull request (`question-loop` into `main`) once happy with the diff.
5. **Review `data/question_bank.json`**: difficulty labels are my judgement. Python basics, Data structures and Databases and SQL have 6 questions each, so 8 or 10 question interviews end early there.
6. A server from your terminal is listening on port 8001 (PID 28719). I never touched it. The old note about port 8000 is gone: nothing listens there now.

**Not done / pending (by choice or limits):** real Gemini under load (free tier), the whole real pipeline under load, the Whisper plateau experiment, timeout tuning from real data, `.env` auto-loading (still: `source .env`).

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
