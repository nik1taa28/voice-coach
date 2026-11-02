"""Send ONE utterance so the server loads its ASR model before the measured run, and show what it transcribed.

    LOADTEST_WAV=loadtest/audio/answer.wav python loadtest/warmup.py http://127.0.0.1:8010

The session is idle, so the server transcribes only (no LLM). That one cold transcription does land in the server's asr
samples; the cold time is printed here so it can be reported separately.
"""
import json
import os
import sys
import time
import wave

import websocket  # the websocket-client package

SAMPLE_RATE = 16000


def main(host: str) -> None:
    wav_path = os.environ.get("LOADTEST_WAV")
    if not wav_path:
        raise SystemExit("LOADTEST_WAV is required")
    with wave.open(wav_path, "rb") as wav:
        shape = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
        if shape != (SAMPLE_RATE, 1, 2):
            raise SystemExit(f"{wav_path} must be 16000 Hz, mono, 16-bit; got (rate, channels, bytes) = {shape}")
        pcm = wav.readframes(wav.getnframes())

    ws = websocket.create_connection(host.replace("http", "ws", 1) + "/ws", timeout=120)  # model load can be slow
    try:
        json.loads(ws.recv())  # {"type": "session"}
        started = time.perf_counter()
        ws.send(json.dumps({"type": "utterance_start", "sample_rate": SAMPLE_RATE}))
        for i in range(0, len(pcm), 8192):
            ws.send_binary(pcm[i : i + 8192])
        ws.send(json.dumps({"type": "utterance_end"}))
        while True:
            message = json.loads(ws.recv())
            if message["type"] == "transcript":
                break
            if message["type"] == "error":
                raise SystemExit(f"warm-up failed: {message.get('reason')}")
    finally:
        ws.shutdown()  # not close(): see the comment in locustfile.py
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"warm-up: provider={message['provider']} audio_ms={message['audio_ms']:.0f} asr_ms={message['asr_ms']:.0f} "
          f"(cold, includes model load) client_ms={elapsed_ms:.0f}\nwarm-up transcript: {message['text']!r}")


if __name__ == "__main__":
    main(sys.argv[1])
