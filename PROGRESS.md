# Progress

## Summary (read this first)

**Read first:** `docs/PROJECT_BRIEF.md` (sections 2, 6 and 8), then `docs/performance.md`. Current branch: `main`; the `question-loop` pull request (#1) is merged (`86c1133`).

| | Status |
|---|---|
| M0 skeleton | Done, committed (`de0fd7a`), tests pass |
| M1 mic -> ASR | Done, committed (`adc8169`), tests pass; real Whisper verified by hand |
| M2 LLM + state machine | Done, committed (`360bfd0`), tests pass; real Gemini verified once by hand |
| M3 TTS + pipelining | Done, committed (`df16505`), tests pass; verified by hand in one browser |
| M4 reliability | Done, committed (`74ce23c`), tests pass; verified by hand on real uvicorn (fake providers) |
| M5 observability | Done, committed (`6e1787a`). Metrics, `/metrics`, JSON logs, dashboard, Locust load test, baseline report |
| M6 Docker / CI / README numbers | **Written, partly verified.** CI recipe verified in a clean venv; demo mode from a clean venv. **Docker image built and run locally on 2026-11-03** (demo mode: 25 s build, 251 MB, healthy, full interview through the container; the `WITH_ASR=1` image is not built) and **CI run on GitHub once (user-reported, 2026-11-03): a green tick on the Actions page for the first push of `question-loop`**. Repo: https://github.com/nik1taa28/voice-coach (`main` = M0 only; `question-loop` has everything, pull request opened by the user). Which jobs ran, durations and the run number were not reported |
| Curated question bank | Done (`a14f113`, moved to `data/` in `2f91d68`). 86 questions, 7 topics (+21 on 2026-11-10: every topic now has at least 11). Summary prompt fixed after two real Gemini runs (`bb79da9`) |
| Code-owned loop | Done (`0bb9a1b`..`1c54177`). JSON follow_up / move_on with fallback, follow-up cap in code, automatic end after 5/6/8/10 questions, at the time limit or when the bank runs out, `ending` state. Borrowing from the nearest level (`805f13a`). One real Gemini session: 2 valid decisions, 1 invalid-reply fallback (**cause pending**) |
| Delivery feedback (stretch) | Done (`2d4b84c`, `6f09676`, docs). Words per minute, fillers, pauses, length; measured by code, never by the LLM. One real session in Chrome with a real microphone worked (user-reported 2026-11-10) |

**Tests:** `308 passed in 5.79s` (`.venv/bin/python -m pytest -q`), run by Claude on 2026-11-10.

**Done by you on 2026-11-10 (user-reported):** CI run succeeded; one real Chrome session (real Whisper, Gemini and browser voice) worked end to end, the time limit ended it, the recording look appeared. The `invalid reply` log line could not be found, so that cause stays unknown.

**Needs you (in order of value):**
1. **Speech recognition accuracy:** `tiny.en` misheard several technical words. Try `ASR_MODEL=base.en` (then `small.en`) with the same session and compare. Say what you see; latency with the bigger models is not measured yet.
2. Look at dark mode, a phone-width window and a second browser (Safari or Firefox).
3. Optional: the `WITH_ASR=1` Docker image.
4. Read the 21 new questions in `data/question_bank.json` (my wording; difficulty labels are my judgement).
5. Delete the merged `fix-test-isolation` branch; decide about `.vscode/`.

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
- The `WITH_ASR=1` Docker image (not built) and per-job CI details (only a user-reported green tick).
- Dark mode, phone width and a second browser for the new pages.

(M6, the question bank and delivery feedback are done; see the table above.)

## Housekeeping

- `.vscode/` is untracked.
- `loadtest/results/` subfolders: `archive-1000-window/` (runs with the old 1000-sample window, valid), `invalid-u100-*` (the two hung runs), `experiment-cpu-burners/`, `smoke/`. `summarize.py` reads only the top-level files.
