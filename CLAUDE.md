# Spoken Mock-Interview Coach (portfolio project)

I'm a 3rd-year CSE student preparing for placements. I want practical experience with backend engineering,
async Python, real-time communication, reliability, observability, testing and DevOps. I must be able to
explain and defend every part in an interview, so teach me as we build. Do not dump a whole repo.

## Product (keep it tiny)
A spoken mock-interview coach: the coach asks a question out loud, I answer by microphone, the app transcribes
it, an LLM replies with a short follow-up, the app speaks it. Simple prompt + conversation history only.
Controls: start/pause/end, topic + difficulty, visible transcript, short end summary. Demo mode with fake
providers must work with no credentials. No RAG, no vector DBs, no agent frameworks, not a general assistant.
Questions come from a curated JSON question bank (topic, difficulty, key points), not invented by the LLM. The LLM only writes follow-ups and feedback.
Stretch (after M4): delivery feedback from measurable signals only (words per minute, filler words, pauses, answer length); no emotion-detection ML.

## Constraints
- FREE ONLY. No paid services. I may have a free Gemini API key; never hardcode keys (.env.example + .gitignore).
- Backend: Python + FastAPI + WebSockets (async). Frontend: one plain HTML + vanilla JS page. No React/TypeScript.
- ASR / LLM / TTS each sit behind a small provider interface with a deterministic fake for tests and demo mode.
  Defaults to evaluate: local faster-whisper (ASR), Gemini free tier or local Ollama (LLM), browser speech or
  local Piper (TTS). Explain latency/privacy/browser-compat trade-offs before choosing. Never present a fake as real.
- No Redis, brokers, Kubernetes, microservices. Add infrastructure only for a stated need.

## Modules (one at a time, never all at once)
M0 skeleton: FastAPI app, GET /health, WebSocket echo with session id, first pytest, first docs.
M1 mic audio -> WebSocket -> speech recognition, timed.
M2 LLM follow-up (streaming if supported), conversation history, session state machine, timed.
M3 text-to-speech + full pipeline + per-stage latency budget table.
M4 reliability: timeouts, cancellation, fallbacks, safe retries, session cleanup; tests with slow/failing fakes.
M5 observability: structured logs with session ids, a /metrics endpoint, a small HTML dashboard page showing p50/p95 latency per stage and error rate, load test (Locust or k6), baseline report with real numbers.
M6 Docker, GitHub Actions CI, README with verified numbers.
M0-M4 + README is a complete project; M5-M6 may be lighter if time runs short.

## How to teach, every module
1. Before code: one Mermaid diagram of what we're building + a short plain-English explanation
   (what problem it solves, what happens at runtime, what breaks without it, how I'd say it in an interview).
   Keep it to about one screen. No textbook chapters.
2. Design: files to create, each file's job, key function signatures, failure cases.
3. Implement one small cohesive piece. Explain only meaningful decisions, not obvious lines.
4. Give exact run/test commands and the expected output. I run them and paste results. Never claim a test
   passed unless you ran it. If a test fails, help me understand the cause.
5. Append to docs/learning-notes.md (mostly diagrams): the diagram(s), concise points, then 1 key concept,
   1 interview question with a short answer, 1 exercise, 1 common mistake. Append only; don't rewrite old notes.

## Docs
docs/architecture.md (mark each part Planned / Implemented / Verified), docs/learning-notes.md (include design
decisions: choice, alternatives, trade-off), docs/performance.md (from M5; measured numbers only).

## Engineering rules
- Before any command that installs, modifies or starts something, say what it does and why.
- Read existing code before editing. Small understandable changes. No dependency without a stated purpose.
- No broad except blocks that hide errors. Don't skip tests.
- Measure time with monotonic clocks, per stage. Never invent numbers; unrun experiments stay "pending".
- Don't claim streaming if a stage waits for a complete result.
- Keep replies short and visual. Maintain a short checklist: done / verified / remaining.
