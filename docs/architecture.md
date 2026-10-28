# Architecture

Status key: **Planned** / **Implemented** (code exists) / **Verified** (tests run and passed).

## M0 system

```mermaid
flowchart LR
  B[Browser / test client] -- "GET /health" --> F[FastAPI app]
  F -- '{"status":"ok"}' --> B
  B <-- "WebSocket /ws" --> F
  F --> S[sessions.py: new_session_id, build_echo, build_error]
```

| Part | Status | Notes |
|---|---|---|
| `GET /health` | Verified | `tests/test_app.py::test_health` |
| `WS /ws` session id on connect | Verified | 32-char uuid4 hex; unique per connection |
| `WS /ws` JSON echo + `server_ts` (UTC ISO 8601) | Verified | |
| Error replies for invalid / non-object JSON (connection stays open) | Verified | |
| Real uvicorn server run by hand | Pending | user runs commands from the M0 walkthrough |
| Mic audio -> ASR (M1) | Planned | |
| LLM follow-up + state machine (M2) | Planned | |
| TTS + latency budget (M3) | Planned | |
| Reliability (M4) | Planned | |
| Logs / metrics / load test (M5) | Planned | |
| Docker / CI (M6) | Planned | |

## WebSocket message protocol (M0)

Server -> client on connect: `{"type":"session","session_id":"<hex>"}`
Client -> server: any JSON object.
Server -> client: `{"type":"echo","session_id","data":<your object>,"server_ts":"<iso>"}`
Errors: `{"type":"error","session_id","reason":"invalid JSON" | "JSON must be an object"}`
