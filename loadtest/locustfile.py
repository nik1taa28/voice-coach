"""Load test: each virtual user runs whole interviews over /ws (start, a few spoken answers, end, summary).

Run (server already started, e.g. `python -m uvicorn backend.main:app`):
    locust -f loadtest/locustfile.py --headless -u 10 -r 2 -t 60s --host http://127.0.0.1:8000

Timings here are the CLIENT's view (monotonic clock, includes network and Locust's own work). The server's per-stage view is
saved from GET /metrics when the test stops. The two are never subtracted from each other.
Audio is sent as fast as the socket accepts it, not paced like a microphone: what is measured is the time AFTER "stop".
"""
import json
import os
import time
import urllib.request
import wave
from pathlib import Path

import websocket  # the websocket-client package
from locust import User, between, events, task

SAMPLE_RATE = 16000
CHUNK_BYTES = 8192
RECV_TIMEOUT_S = float(os.environ.get("LOADTEST_RECV_TIMEOUT_S", "40"))  # longer than the server's own 30 s deadlines
ANSWER_SECONDS = float(os.environ.get("LOADTEST_ANSWER_S", "3"))
ANSWERS_PER_INTERVIEW = int(os.environ.get("LOADTEST_ANSWERS", "3"))
AUDIO_WAV = os.environ.get("LOADTEST_WAV")  # 16 kHz mono PCM16 wav with real speech; needed for ASR_PROVIDER=whisper


def load_pcm() -> bytes:
    if AUDIO_WAV is None:
        return b"\x00\x00" * int(SAMPLE_RATE * ANSWER_SECONDS)  # silence: fine for the fake ASR, which ignores the content
    with wave.open(AUDIO_WAV, "rb") as wav:
        shape = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
        if shape != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{AUDIO_WAV} must be 16000 Hz, mono, 16-bit; got (rate, channels, bytes) = {shape}")
        return wav.readframes(wav.getnframes())


PCM = load_pcm()


class LoadError(Exception):
    """The server answered with an `error` message, or closed the conversation badly."""


class InterviewUser(User):
    wait_time = between(1, 3)  # pause between whole interviews

    def report(self, name: str, started: float, exc: Exception | None = None) -> None:
        self.environment.events.request.fire(
            request_type="WS", name=name, response_time=(time.perf_counter() - started) * 1000,
            response_length=0, exception=exc, context={},
        )

    @staticmethod
    def recv(ws) -> dict:
        message = json.loads(ws.recv())
        if message.get("type") == "error":
            raise LoadError(message.get("reason", "error without reason"))
        return message

    def await_reply(self, ws, started: float, label: str, wait_for_transcript: bool) -> None:
        """Read until coach_done, reporting the time (since `started`) of each milestone on the way."""
        seen_delta = False
        while True:
            message = self.recv(ws)
            kind = message["type"]
            if kind == "transcript" and wait_for_transcript:
                self.report(f"{label} -> transcript", started)
            elif kind == "coach_delta" and not seen_delta:
                seen_delta = True
                self.report(f"{label} -> first_delta", started)
            elif kind == "coach_done":
                self.report(f"{label} -> coach_done", started)
                return

    def send_answer(self, ws) -> float:
        ws.send(json.dumps({"type": "utterance_start", "sample_rate": SAMPLE_RATE}))
        for i in range(0, len(PCM), CHUNK_BYTES):
            ws.send_binary(PCM[i : i + CHUNK_BYTES])
        ws.send(json.dumps({"type": "utterance_end"}))
        return time.perf_counter()  # the "stop press" moment

    @task
    def interview(self) -> None:
        interview_started = time.perf_counter()
        url = self.host.replace("http", "ws", 1) + "/ws"
        try:
            ws = websocket.create_connection(url, timeout=RECV_TIMEOUT_S)
        except (OSError, websocket.WebSocketException) as exc:
            self.report("connect", interview_started, exc)
            return
        try:
            self.recv(ws)  # {"type": "session"}
            started = time.perf_counter()
            ws.send(json.dumps({"type": "start", "topic": "Python", "difficulty": "easy"}))
            self.await_reply(ws, started, "opener", wait_for_transcript=False)
            for _ in range(ANSWERS_PER_INTERVIEW):
                time.sleep(0.5)  # a person does not answer in the same millisecond the coach finishes
                started = self.send_answer(ws)
                self.await_reply(ws, started, "answer", wait_for_transcript=True)
            started = time.perf_counter()
            ws.send(json.dumps({"type": "end"}))
            while self.recv(ws)["type"] != "summary":
                pass
            self.report("summary", started)
            self.report("interview (whole)", interview_started)
        except (LoadError, OSError, websocket.WebSocketException) as exc:
            self.report("interview (whole)", interview_started, exc)
        finally:
            # NOT ws.close(): its close handshake ends in a bare `except: pass` (websocket/_core.py), which also swallows the
            # GreenletExit Locust raises to stop this user. A user killed inside close() carried on with its next interview, and
            # Locust waited forever for it (hung the 100-user step twice). shutdown() just closes the socket and cannot do that.
            ws.shutdown()


@events.test_stop.add_listener
def save_server_metrics(environment, **_kwargs) -> None:
    """Fetch the server's own view once the run ends. Restart the server between runs: its window and counters are cumulative."""
    if not environment.host:
        return
    try:
        with urllib.request.urlopen(environment.host + "/metrics", timeout=5) as response:
            body = response.read()
    except OSError as exc:  # URLError is an OSError
        print(f"could not fetch /metrics: {exc}")
        return
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    label = os.environ.get("LOADTEST_LABEL", "run")
    path = out_dir / f"metrics-{label}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_bytes(body)
    print(f"server /metrics saved to {path}")
