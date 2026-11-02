# Performance (M5 baseline)

Measured numbers only. Anything not run is marked **pending**. Raw files: `loadtest/results/` (Locust CSVs, `/metrics` snapshots, server logs). Re-create the tables with `.venv/bin/python loadtest/summarize.py`.

## Setup

| | |
|---|---|
| Date | 2026-11-01 |
| Code | commit `74ce23c` (M4) plus the uncommitted M5 working tree |
| Machine | Apple M3, 8 cores, 16 GB, macOS 14.3, Python 3.13.6 |
| Load generator | Locust 2.47.0 + websocket-client 1.9.2, **on the same laptop as the server** |
| Server | one `uvicorn` process (one worker), started fresh for every step, JSON logging on, `METRICS_WINDOW=200000` (every sample of a step is kept) |
| Users | closed loop: each user runs a whole interview (opener, 3 answers of 7.9 s or 3 s audio, End, summary), 0.5 s before each answer, 1-3 s between interviews. Next request only after the reply arrives |
| Step | 60 s, ramp 10 users/s |
| Two views | **client** = Locust, `answer -> coach_done` (includes network, JSON, Locust itself). **server** = `/metrics` per stage (`perf_counter` inside the app). Different views of the same requests; never subtract one from the other |
| Percentiles | server: exact nearest-rank over every sample of the step. Client: Locust's CSV (rounded buckets) |

## A. Fake providers: our own overhead (3 repeats per step)

ASR, LLM and TTS are the deterministic fakes. Values are min-max across the 3 repeats. The fake reply is one sentence, so `first_speak` equals `llm_total`, and `llm_ttft` is about 0.0-0.1 ms.

| Users | Answers per step | Locust req/s | Failures | Client p50 (ms) | Client p95 (ms) | Server llm_total p50 / p95 (ms) | Server tts p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|
| 1 | 48-53 | 3.5-3.9 | 0 | 31 | 31-34 | 15.2-15.8 / 15.9-18.0 | 14.3-14.8 / 15.1-17.1 |
| 10 | 494-502 | 36-37 | 0 | 21-23 | 37-38 | 8.4-8.8 / 15.6-16.5 | 8.0-8.3 / 14.6-15.5 |
| 50 | 2396-2403 | 176 | 0 | 12-13 | 21-25 | 4.8-4.9 / 6.1-6.3 | 4.6-4.7 / 5.8-6.0 |
| 100 | 4580-4619 | 336-338 | 0 | 14 | 29-35 | 4.7-4.8 / 5.0-5.1 | 4.5-4.6 / 4.7-4.8 |

What this shows, and what it does not:
- **Measured:** 0 failures in 12 runs. Throughput grew about linearly up to 100 users, and no `CPU usage` warning appeared. **No saturation point was found with fake providers up to 100 users.** The repeats agree closely, so run-to-run noise is small.
- **200 users, 1 run (earlier window setting, archived):** Locust reported `CPU usage above 90%`, throughput was 413 req/s (not double), and client p50 rose to 230 ms while the server's `llm_total` p50 stayed 4.9 ms. That run is **generator-limited**, so it is not a server capacity figure, and where those 225 ms went is **unexplained**.
- **Most of the server time here is the fake TTS** (tts p50 is nearly all of llm_total). These numbers are our overhead with fakes. They say nothing about real Gemini.

### The "latency falls as users rise" effect

At 1 user the server stages are about 3x slower than at 50-100 users, in all 3 repeats. One experiment (1 run, not a repeat): the 1-user step with two busy-loop processes on other cores.

| 1-user step | Client p50 (ms) | Server llm_total p50 (ms) | Server tts p50 (ms) |
|---|---|---|---|
| 3 normal runs | 31 / 31 / 31 | 15.2 / 15.7 / 15.8 | 14.3 / 14.7 / 14.8 |
| with 2 busy loops | 12 | 5.0 | 4.8 |

So it is a **machine effect**: the same single-threaded CPU-bound code ran about 3x faster when other cores were busy. **Not measured:** the CPU frequency, or which mechanism (frequency scaling, core scheduling). Treat the 1- and 10-user rows as "an almost idle laptop". The Whisper 1-2 user rows may be affected the same way; **untested**.

## B. Real faster-whisper (`tiny.en`, int8, CPU); LLM and TTS are fakes; 1 run per step

Audio: one 7.89 s clip made with the macOS `say` voice (**synthetic speech, not a human**), 16 kHz mono. Each fresh server was warmed up with one transcription first (not counted in the client numbers).

| Users | Answers per step (per s) | Failures | Client p50 / p95 (ms) | Server asr p50 / p95 (ms) | Server llm_total p50 / p95 (ms) |
|---|---|---|---|---|---|
| 1 | 42 (0.7) | 0 | 260 / 330 | 242 / 330 | 5.1 / 16.2 |
| 2 | 81 (1.4) | 0 | 250 / 380 | 240 / 387 | 5.0 / 15.4 |
| 4 | 155 (2.6) | 0 | 350 / 570 | 337 / 566 | 5.6 / 8.5 |
| 8 | 245 (4.1) | 0 | 680 / 1200 | 674 / 1244 | 6.1 / 7.9 |
| 16 | 231 (3.9) | 0 | 2900 / 3700 | 2868 / 3655 | 6.4 / 8.6 |
| 32 | 219 (3.6) | 0 | 7200 / 8500 | 7167 / 8480 | 7.2 / 9.6 |

Server `asr n` is slightly above the client answer count (for example 44 vs 42 at 1 user): it includes the warm-up transcription and answers that were in flight when Locust stopped.

Cold start (single samples per fresh server): model load 465-577 ms (server log), first transcription including load 788-898 ms (6 servers). Warm one-user decode 242 ms for 7.9 s of audio.

What this shows:
- **Measured:** throughput stops growing at about 4 answers per second (4.1 at 8 users, then 3.9 and 3.6). Past that point **latency grows with the number of users** instead: asr p50 674 ms (8 users), 2868 ms (16), 7167 ms (32).
- **Measured:** the client time is almost all ASR (client p50 is within a few tens of ms of server asr p50 at every step; Locust rounds its larger values, so the exact gap is uncertain), so the wait is queueing in the Whisper stage, not the network or the web layer.
- **Measured:** no failures, no ASR timeouts. The worst p95 (8.5 s) is far below the 30 s ASR deadline, so M4's deadline never fired in this test. Whether 30 s is a good value is **still undecided**.
- **Measured:** the other server stages stayed small while up to 32 answers waited for ASR (`llm_total` p95 at most 16.2 ms, no growth with users). That is consistent with the M1 claim that `asyncio.to_thread` keeps the event loop free. It does not prove Pause or End stay responsive under that load; that was not tested.
- **Interpretation, untested:** the plateau at about 1 / 0.242 s = 4.1 answers per second matches "one transcription at a time", for example faster-whisper's default of one worker. A test would change that setting and rerun 8-16 users.

## C. The 100-user hang (a test-tool bug, fixed)

- Before the fix: the 100-user step hung at shutdown in 2 of 2 runs (9½ min, then 81 s+ before I stopped it).
- Cause: `ws.close()` in websocket-client ends in a bare `except: pass`, which swallowed the `GreenletExit` Locust raises to stop a user. Reproduced in isolation (greenlet survives a kill during `close()`). Fix: `ws.shutdown()`.
- After the fix: 24 of 24 runs exited by themselves (100-user steps: 0 of 6 hung). Good evidence for a probabilistic failure, not proof.

## What was not measured (pending)

- **Real Gemini under load.** Not planned: free-tier rate limits, and the key would be at risk.
- **The whole real pipeline** (Whisper + Gemini + voice) under any load, or even once end to end.
- **Real browser voice** latency under load (the server cannot time `speechSynthesis`).
- **Open-loop load** (a fixed arrival rate regardless of replies). All tests here are closed-loop, so the latency at 32 Whisper users reflects at most 32 waiting requests.
- **Separate machines** for the load generator and the server.
- **Memory and CPU usage** of the server (only Locust's CPU warning was watched).
- **Longer runs** than 60 s, and many concurrent sessions held open.
- **Timeout tuning:** the M4 deadlines are unchanged until real-provider numbers exist.
