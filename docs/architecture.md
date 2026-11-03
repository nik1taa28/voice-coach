# Architecture

Status key: **Planned** / **Implemented** (code exists) / **Verified** (tests run and passed, or run by hand with the result recorded).

## System as of M3 (the diagram below shows the M1 audio path; the M3 reply path is under "Reply path and latency budget")

```mermaid
flowchart LR
  subgraph Browser
    Mic[getUserMedia] --> W[AudioWorklet batcher]
    W --> D[downsample to 16 kHz PCM16]
  end
  D -- "binary frames + utterance_start/end JSON" --> WS[FastAPI /ws]
  WS --> U[UtteranceBuffer<br/>sessions.py]
  U -- "complete utterance" --> T[transcribe_timed<br/>perf_counter]
  T --> P{{ASRProvider}}
  P --> F[FakeASR]
  P --> H[WhisperASR<br/>asyncio.to_thread]
  T -- "transcript + timings" --> WS
```

| Part | Status | Notes |
|---|---|---|
| `GET /health` | Verified | `tests/test_app.py::test_health` |
| `GET /` serves `frontend/index.html` | Verified | `tests/test_frontend.py`; page used by hand |
| `WS /ws` session id, JSON echo, error replies | Verified | M0 tests |
| Real uvicorn server run by hand | Verified | needed `websockets` installed (added to `requirements.txt`); TestClient could not catch this |
| PCM16 validation (`audio.py`) | Verified | `tests/test_audio.py` |
| `ASRProvider` interface, `FakeASR`, provider selection | Verified | `tests/test_audio.py`, `tests/test_whisper.py` |
| Utterance protocol + per-chunk size cap + timing | Verified | `tests/test_asr_ws.py` |
| Browser mic capture + 16 kHz resample (fake provider) | Verified (by hand) | user-reported working; `audio_ms` matched the time recorded |
| `WhisperASR` (faster-whisper `tiny.en`, int8, CPU) | Verified | unit tests with a stub model; real model run by hand, transcript correct |
| Whisper latency numbers | Partial | M1 single samples (see `learning-notes.md`); M5 added a load run, see `docs/performance.md`: warm one-user decode 242 ms for 7.9 s of synthetic speech, cold first transcription 788-898 ms, throughput plateau about 4 answers/s |
| Streaming ASR | Not planned for M1 | ASR waits for the complete utterance; audio is streamed *in*, text is not streamed *out* |
| Tail of audio (< ~43 ms) dropped at stop | Known limitation | worklet buffer is not flushed on stop |
| `.env` auto-loading | Planned | env vars must be exported in the shell for now |
| `LLMProvider` interface, `FakeLLM`, `prompts.py` (M2 piece 1) | Verified | `tests/test_llm.py`; the fake labels itself and its summary as fake |
| `InterviewSession` state machine (M2 piece 1) | Verified | `tests/test_state.py`; pure, not yet wired to `/ws` |
| `GeminiLLM` streaming (M2 piece 2) | Verified | mocked-HTTP tests (`tests/test_gemini.py`) plus one real call by hand: `gemini-2.5-flash` accepted, reply arrived in 2 pieces |
| WS wiring: start/pause/resume/end, `coach_delta`, TTFT + total timing, summary (M2 piece 3) | Verified (fake LLM only) | `tests/test_llm_ws.py`; in M2/M3 pause/end waited for the turn to finish (inline await). **Superseded by M4**: they now cancel the turn (see the M4 rows). Two tests here were changed in M4 so their injected failure outlasts the retry |
| Recording allowed in `idle` (M1 transcribe-only) and `listening` | Implemented | keeps M1 behaviour; rejected while paused/thinking/ended |
| Frontend controls + transcript (M2 piece 4) | Verified (by hand, fake providers) | user-reported: start, opening question, one spoken answer, follow-up, end, summary all worked with the FAKE banner. Pause/resume in the browser **not reported**. `tests/test_frontend.py` only checks control ids |
| Real Gemini latency (M2) | Partial | one sample: ttft 3129 ms, total 3283 ms (standalone script, opener prompt, user's machine and network). Distribution **pending** (M5). Not yet run through `/ws` in the browser, and pause/resume in the browser not reported |
| `SentenceSplitter` (M3 piece 1) | Verified | `tests/test_sentences.py` (9). Small abbreviation list; "J. Smith" would split wrongly |
| `TTSProvider`, `FakeTTS` (labelled beep), `BrowserTTS` (text only), `TTS_PROVIDER` selection (M3 piece 2) | Verified | `tests/test_tts.py` (9) |
| WS wiring: `speak` per sentence, `first_sentence_ms` / `first_speak_ms`, one `speech failed` error per turn, `playback_started` (M3 piece 3) | Verified (tests, fake/scripted providers) | `tests/test_tts_ws.py` (14): order, speech starts before the LLM finishes, TTS failure keeps text and history, LLM failure mid-reply does not speak the tail |
| Frontend speech queue, cancel rules, per-stage FAKE labels, browser-clock `stop->audio` (M3 piece 4) | Verified (by hand, one session each) | user-reported: fake beep heard, real browser voice heard, opener spoken without an extra click, voice cut off mid-sentence by Pause / Start recording, pause + resume worked. Browser: VS Code's built-in browser, user guessed Chromium ("chrome i guess"), **not confirmed**. Real Chrome, Safari, Firefox: **not tested** |
| TTS runs inline in the LLM stream loop | Known limitation | a slow server-side TTS delays reading the next LLM piece, and `llm_total_ms` includes it. M4 added a per-sentence deadline (`TIMEOUT_TTS_S`) so it cannot hang forever, but it is still inline. A queue + worker task would fix the coupling; cancellation (M4) now makes that possible, no stated need yet |
| Streaming claim (M3) | Stated precisely | LLM -> splitter -> TTS dispatch is per piece / per sentence. Each TTS call waits for one **complete sentence**; not token-level TTS. ASR still waits for the full utterance |
| Spoken summary | Not done | the end summary is text only |
| Piper (server-side TTS) | Not planned for M3 | the interface allows it later (`SpokenChunk.audio` carries bytes) |
| Real Whisper + Gemini + voice through the whole pipeline | Pending | the only M3 pipeline samples used the fake ASR and LLM (see budget table) |
| Latency budget (M3) | Partial | table below: two single samples with fake upstream stages; real numbers **pending** |
| Deadlines per stage (`Timeouts`, `with_deadline`, `StageTimeout`) (M4 piece 1) | Verified | `tests/test_reliability.py`; values are env-overridable starting guesses, not measurements |
| LLM retry once, only before the first piece (`stream_with_retry`) (M4 piece 1) | Verified | `tests/test_reliability.py`, `tests/test_cancel_ws.py`; a permanent error (bad key) is retried too and costs 0.3 s |
| `TurnRunner`, `InterviewSession.cancel_turn` (M4 pieces 1-2) | Verified | unit tests |
| Turn task + `SafeSender` + disconnect cleanup in `main.py` (M4 piece 3) | Verified (tests, TestClient, fake providers) | `tests/test_cancel_ws.py` (14): pause/end mid-reply, pause during ASR, busy, hung LLM/ASR/TTS/summary, flaky LLM, unexpected error, disconnect releases the LLM stream |
| Pause / End buttons enabled while thinking (M4) | Implemented | `frontend/index.html` `refresh()`; `tests/test_frontend.py` only checks control ids |
| Real uvicorn run of M4, fake providers, `FAKE_LLM_DELAY_S=1` (Pause during a slow reply; close the tab mid-reply) | Verified (by hand, one session, user-reported) | Pause mid-reply: the beep stopped and an `error: turn cancelled` line appeared. Resume then a new answer: normal reply. Closing the tab mid-reply: the server logged `disconnected`. **Not reported:** whether the partial coach line disappeared, whether the status read `paused`, and whether the log had a traceback or a `client gone` line. Browser not recorded. Real Gemini/Whisper through the task path: **pending** |
| Whisper worker thread cannot be cancelled | Known limitation | an ASR timeout abandons the result, but the thread keeps using CPU until it finishes; audio is capped at 60 s. Real fix = a subprocess, out of scope |
| No fallback provider (e.g. LLM -> second LLM, server TTS -> browser) | Not done | the only "fallbacks" are: retry once (LLM), text-only reply (TTS failure) |
| Cross-session limits, queueing under load | Measured, not implemented | under real Whisper, throughput plateaus at about 4 answers/s and asr p50 grows 674 ms (8 users) -> 7167 ms (32 users) with 0 failures and no timeouts (`docs/performance.md`). There is still no limit or queue policy in the app |
| `Metrics` registry (p50/p95 per stage, outcome counters) and `GET /metrics` (M5 pieces 1-2) | Verified (tests) | `tests/test_metrics.py` (12), `tests/test_metrics_ws.py` (8); **uncommitted**. Window = newest 1000 samples per stage. `/metrics` is open, no auth. Summary failures are not counted in `error_rate`. A failed turn records no latency sample |
| JSON logs with `session_id` via contextvar (M5 piece 3) | Verified (tests + by hand) | `tests/test_logs.py` (8). Real uvicorn run (fake providers): the same `session_id` on every `voice_coach` line. uvicorn's own lines stay plain text and carry no id |
| `GET /dashboard` (M5 piece 4) | Verified (tests); by hand partly | `tests/test_dashboard.py` (4). By hand: dark mode fine, the "cannot read /metrics" banner appears when the server stops. The `asr` row filling on screen after a spoken answer: not confirmed |
| Load test: Locust + `loadtest/sweep.sh` + `warmup.py` + `summarize.py` (M5 piece 5) | Verified (by running) | fake providers 1/10/50/100 users x 3 repeats, 0 failures; real Whisper 1-32 users, 0 failures. Load generator on the same laptop. The earlier 100-user shutdown hang was a test-tool bug (`ws.close()` swallows Locust's kill; fixed with `ws.shutdown()`); 24 of 24 runs since exited by themselves. Real Gemini under load: not planned. Whole real pipeline under load: pending |
| Question bank: `question_bank.json` (65 questions, 7 topics), `questions.py` load/validate/pick (bank piece 1) | Verified (tests) | `tests/test_questions.py` (23): valid file, every topic has all 3 levels with at least 2 questions, bad entries and duplicate ids rejected. Content written by me, **not reviewed by anyone else**; difficulty labels are my judgement |
| Bank-driven turns: `TurnPlan`, `plan_turn`, commit in `add_coach`; bank question spoken with no LLM call; LLM follow-ups (2 per question, `FOLLOWUPS_PER_QUESTION`); wrap-up line when a topic and level runs out; `GET /topics`; topic dropdown (bank piece 2) | Verified (tests + one real uvicorn run, fake providers) | `tests/test_state.py`, `test_llm_ws.py`, `test_cancel_ws.py`, `test_tts_ws.py`, `test_metrics_ws.py`, `test_llm.py`, `test_frontend.py`: 236 passed in total. Real server run (port 8001, demo mode): turns went bank, fake, fake, bank, fake; unknown topic rejected in tests. The dropdown in a browser: **not seen** |
| Follow-up prompt with key points; summary prompt lists the questions asked | Verified (by hand, 2 real Gemini sessions, user-reported) | Run 1 (data structures): one follow-up per turn, key points not read out, summary had the 4 parts and asked no question. It wrongly counted an unanswered last question against the candidate and stated a guess about a garbled sentence, so two rules were added to the summary system prompt (`backend/prompts.py`). Run 2 (operating systems, "I don't know" answers, last coach question unanswered): no mention of the unanswered question, no claim built on garbled words. One sample per case, not a distribution. Topic name is still only implied in the summary. Observed, not fixed: an answer cut off mid-sentence ("a thread is a unit of...") got a follow-up on the half answer |
| Bank turns have no LLM timing | Stated | `coach_done.llm_ttft_ms` / `llm_total_ms` are `null` for bank lines and `llm_ttft` / `llm_total` metrics get no sample, so those stages now count only follow-ups. Earlier numbers in `docs/performance.md` measured LLM-written openers and are not changed |
| Baseline report `docs/performance.md` and M5 learning notes (M5 piece 6) | Implemented | measured numbers only; open items listed in the report. `METRICS_WINDOW` (env, default 1000) lets a load test keep every sample of a step |
| Curated question bank (JSON: topic, difficulty, key points; the LLM writes only follow-ups and feedback) | Implemented (see the bank rows above) | `data/question_bank.json` (65 questions, 7 topics; moved from `backend/`, data is not code) loaded by `backend/questions.py`; the opener is a bank question, the LLM writes only follow-ups and the summary. Difficulty labels are the author's judgement, not reviewed yet. Stretch after that: delivery feedback from measurable signals only (words per minute, fillers, pauses, length), **Planned** |
| Demo mode from a clean environment (M6) | Verified (by running) | a fresh virtual environment with only `requirements.txt`: `faster_whisper` not importable, app starts, `/health`, `/`, `/metrics`, `/dashboard` return 200, a WebSocket opener finishes with fake providers. This is the closest local stand-in for the container |
| `Dockerfile`, `.dockerignore` (M6) | Implemented, **not built** | the Docker CLI is installed but no daemon was running. python:3.13-slim, non-root user, `HEALTHCHECK` on `/health`, demo mode by default, optional `--build-arg WITH_ASR=1`. Build and run: pending (commands in `docs/PROJECT_BRIEF.md`) |
| `.github/workflows/ci.yml` (M6) | Implemented; recipe verified locally; **not run on GitHub** | the YAML parses (2 jobs: `test`, `docker`). The `test` job's install and pytest steps were run in a clean virtual environment: 186 passed. Action versions (`checkout@v4`, `setup-python@v5`) not checked against current releases. The load test is not part of CI |
| `pytest` listed in `requirements.txt` | Known imperfection | it is installed in the image although the app does not need it; splitting it out was not worth a new file yet |
| Summary prompt (fix, 2026-11-02) | Fixed; verified by tests only | real Gemini answered the old summary request with another interview question, because the request ended with the coach's own turn and the instruction was only in the system prompt. Now a final user turn asks for four parts (Summary, Strengths, Areas to improve, Practice next), and the system prompt names the session's topic and difficulty so the answers are judged against the level. `tests/test_llm.py` asserts every prompt ends with a user turn and roles alternate; `tests/test_llm_ws.py` checks the prompt the LLM receives over `/ws` carries the topic and difficulty. **Not yet re-run against real Gemini** |
| Real runs by hand, user-reported (2026-11-02) | Partly verified | demo mode with the microphone: all clear. Dashboard: `Turns ok` rises, 'cannot read /metrics' appears when the server stops. Real Whisper + microphone: transcript correct (asr_ms not reported). Real Gemini after fixing the key: replies worked. Pause / End / disconnect with `FAKE_LLM_DELAY_S`: passed. One run used the FAKE ASR by mistake (transcripts read 'fake transcript (N ms of audio)'), so it was not the whole real pipeline |
| Stop press -> first sound with a real LLM, user-reported single samples | Partial | fake ASR + Gemini + browser voice: 2507 ms and 2455 ms. Later run (ASR provider not confirmed in the pasted lines): 2041 ms, then 1815 ms. One server line from that run: ttft_ms 1615, first_speak_ms 1616, total_ms 1830. Summary call: total_ms 1226. Single samples on one machine, not a distribution; the whole pipeline with confirmed Whisper is still pending |

## WebSocket message protocol

Server -> client on connect: `{"type":"session","session_id":"<hex>"}`

Client -> server:
- `{"type":"utterance_start","sample_rate":16000}` (only 16000 accepted)
- binary frames: raw PCM16 little-endian mono
- `{"type":"utterance_end"}`
- `{"type":"start","topic","difficulty"}` (difficulty: easy/medium/hard), `{"type":"pause"}`, `{"type":"resume"}`, `{"type":"end"}`
- any other JSON object: echoed (M0 behavior)

Server -> client:
- `{"type":"transcript","session_id","text","provider","audio_ms","asr_ms","rtf"}`; `provider` is `fake` or `faster-whisper`
- `{"type":"state","session_id","state"}`: idle / listening / thinking / paused / ended
- `{"type":"coach_delta","session_id","text"}` (streamed pieces), then `{"type":"coach_done","session_id","text","provider","llm_ttft_ms","llm_total_ms"}`; on failure an `error` follows and the client must discard unfinished deltas
- `{"type":"summary","session_id","text","provider","llm_ttft_ms","llm_total_ms"}`: sent once complete, not streamed; `provider` is `none` when no answers were recorded
- `{"type":"speak","session_id","seq","text","provider","tts_ms"}` plus `mime` and `audio_b64` when the server synthesised audio (`fake`); `provider` is `fake` or `browser`; `tts_ms` is `null` for `browser` (the server did no synthesis). One per sentence, in `seq` order, sent before `coach_done`. After an `error` with reason `coach reply failed` the client must discard queued speech as well as unfinished deltas
- `coach_done` also carries `tts_provider`, `first_sentence_ms` (turn start -> first complete sentence) and `first_speak_ms` (turn start -> first `speak` sent, `null` if nothing was spoken); server clock
- `{"type":"echo","session_id","data","server_ts"}`
- `{"type":"error","session_id","reason"}`; the connection stays open

Client -> server (M3): `{"type":"playback_started","stop_to_audio_ms":<0..600000>}`, sent once per answer when sound actually starts. Measured on the **browser** clock (stop press -> first audible sound); the server only logs it and never subtracts it from server timings. No reply on success; invalid values get an `error`.

Error reasons: `invalid JSON`, `JSON must be an object`, `audio received before utterance_start`, `utterance_end without utterance_start`, `no audio received`, `PCM16 data must have an even number of bytes`, `sample_rate must be 16000`, `utterance already in progress; ...`, `utterance longer than 60s` (sent once, further frames dropped until `utterance_end`), `transcription failed`, `coach reply failed`, `summary failed`, `LLM unavailable: ...` (connection then closes), `cannot <event> while <state>`, `empty answer`, `no speech recognized, please try again`, topic/difficulty validation messages, `speech failed` (sent once per turn; the text and history are unaffected and sentences already queued still play), `stop_to_audio_ms must be a number between 0 and 600000`.

Added in M4: `transcription timed out`, `coach reply timed out`, `summary timed out`, `turn cancelled` (Pause/End interrupted a running turn; the client discards partial text and queued speech, like for `coach reply failed`), `internal error` (an unexpected exception inside a turn; logged with a traceback; the session stays usable), `busy: the previous request is still being processed` (a second utterance or `start` while ASR or a turn is running). A cancelled opener returns the session to `idle`, so Pause then also reports `cannot pause while idle`, preceded by a `state` message.

## Reply path and latency budget (M3)

```mermaid
flowchart LR
  A[stop press<br/>browser clock starts] --> B[ASR<br/>asr_ms]
  B --> C[LLM first piece<br/>llm_ttft_ms]
  C --> D[first sentence complete<br/>first_sentence_ms]
  D --> E[TTS<br/>tts_ms, server voices only]
  E --> F[speak sent<br/>first_speak_ms]
  F --> G[first sound<br/>stop_to_audio_ms<br/>browser clock stops]
```

Server stages use `time.perf_counter()`; `first_sentence_ms` and `first_speak_ms` count from the start of `coach_turn` (after ASR). The last row uses the browser's `performance.now()`. The two clocks are never added or subtracted.

Measured, user's machine (macOS; hardware not recorded), one session per column, **single samples, not a distribution**. ASR and LLM were the fakes, so these are an overhead floor, not the real pipeline:

| Stage | Where | Fake voice (beep) | Browser voice | Real pipeline |
|---|---|---|---|---|
| ASR | server | 0 ms (fake) | 0.1 ms (fake) | earlier M1 sample: 987.6 ms for 7.3 s audio, faster-whisper `tiny.en`; not re-run in M3 |
| LLM first piece | server | 0.1 ms (fake) | 0.1 ms (fake) | earlier M2 sample: 3129 ms, Gemini, standalone; not re-run in M3 |
| First sentence complete (from turn start) | server | 1 ms | 0.8 ms | pending |
| TTS synthesis | server | not shown in the UI (`tts_ms` is in the `speak` message) | n/a (no server synthesis) | pending |
| First `speak` sent (from turn start) | server | 18.3 ms | 0.9 ms | pending |
| LLM total | server | 18.3 ms | 0.9 ms | pending |
| Stop press -> first sound | **browser** | 52 ms | 69 ms | pending |

Reading the table:
- `llm_total_ms` (18.3 vs 0.9 ms) differs between the columns by about the fake TTS cost. This is consistent with TTS running inline in the stream loop, but it compares two different runs and is **not** an isolated measurement.
- With ASR and LLM at about 0 ms, the 52 / 69 ms in the last row is browser-side start-up (audio element or speech engine) plus a few ms of localhost. It says nothing about the real total.
- The first sound waits for the first *complete sentence*, so with a real LLM the budget is dominated by ASR + LLM TTFT + time to finish sentence 1. Targets are not set until real numbers exist (M5).

## Reliability (M4)

```mermaid
flowchart TD
  subgraph Reader["receive loop (always reading)"]
    R[receive frame] --> D{message}
    D -- "start / utterance_end" --> T1[TurnRunner.start]
    D -- "pause / end" --> I[interrupt: cancel task,<br/>cancel_turn if THINKING]
    D -- "disconnect" --> F[finally: cancel task, discard buffer]
  end
  subgraph Turn["turn task (one per session)"]
    A[ASR<br/>with_deadline] --> L[stream_with_retry<br/>first-piece + total deadline<br/>1 retry before any piece]
    L --> S[per sentence TTS<br/>with_deadline]
    S --> O[SafeSender: one lock,<br/>False on closed socket]
  end
  T1 --> A
  I -. CancelledError .-> Turn
  F -. CancelledError .-> Turn
```

| Failure | What happens | Retried? |
|---|---|---|
| ASR error / timeout | `transcription failed` / `transcription timed out`; state unchanged; user records again | no |
| LLM error / timeout **before** the first piece | one retry after 0.3 s; invisible to the client if it works | yes (once) |
| LLM error / timeout **after** the first piece | `coach reply failed` / `coach reply timed out`; `fail_turn` rollback | no (would repeat text already shown) |
| TTS error / timeout | `speech failed` once; rest of the reply is text only | no |
| Summary error / timeout | `summary failed` / `summary timed out`; the session is already `ended` | once, before any output (same rule) |
| Pause / End during a turn | task cancelled, `turn cancelled`, unanswered message dropped, then the event applies | n/a |
| Pause / End while `ended` | rejected; the summary task is left running | n/a |
| Client disconnect | `finally` cancels the task; the LLM HTTP stream is closed | n/a |
| Unexpected exception in a turn | `internal error`, logged with traceback, rollback, session usable | no |

Default deadlines (guesses, env-overridable; only real sample so far: Gemini TTFT 3129 ms once): ASR 30 s, LLM first piece 10 s, LLM total 30 s, TTS 5 s per sentence, summary 30 s.
