import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

from backend.asr import ASRError, get_asr_provider
from backend.audio import AudioError, pcm16_duration_ms
from backend.sessions import UtteranceBuffer, build_echo, build_error, build_transcript, new_session_id

logging.basicConfig(level=logging.INFO)  # uvicorn only configures its own loggers; without this our INFO lines are hidden
logger = logging.getLogger("voice_coach")
app = FastAPI(title="Voice Coach")


INDEX_HTML = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = new_session_id()
    asr = get_asr_provider()
    utterance = UtteranceBuffer()
    logger.info("session %s connected (asr=%s)", session_id, asr.name)
    await websocket.send_json({"type": "session", "session_id": session_id})

    while True:
        incoming = await websocket.receive()
        if incoming["type"] == "websocket.disconnect":
            break

        if incoming.get("bytes") is not None:
            try:
                utterance.add(incoming["bytes"])
            except AudioError as exc:
                await websocket.send_json(build_error(session_id, str(exc)))
            continue

        try:
            message = json.loads(incoming["text"])
        except json.JSONDecodeError:
            await websocket.send_json(build_error(session_id, "invalid JSON"))
            continue
        if not isinstance(message, dict):
            await websocket.send_json(build_error(session_id, "JSON must be an object"))
            continue

        kind = message.get("type")
        try:
            if kind == "utterance_start":
                utterance.start(message.get("sample_rate"))
            elif kind == "utterance_end":
                finished = utterance.finish()
                if finished is not None:
                    pcm, rate = finished
                    reply = await transcribe_timed(asr, session_id, pcm, rate)
                    await websocket.send_json(reply)
            else:
                await websocket.send_json(build_echo(session_id, message))
        except AudioError as exc:
            await websocket.send_json(build_error(session_id, str(exc)))
        except ASRError:
            logger.exception("session %s: ASR failed", session_id)
            await websocket.send_json(build_error(session_id, "transcription failed"))

    logger.info("session %s disconnected", session_id)


async def transcribe_timed(asr, session_id: str, pcm: bytes, sample_rate: int) -> dict:
    """Run ASR and time only that stage, with a monotonic clock."""
    audio_ms = pcm16_duration_ms(pcm, sample_rate)
    started = time.perf_counter()
    transcript = await asr.transcribe(pcm, sample_rate)
    asr_ms = (time.perf_counter() - started) * 1000
    logger.info("session %s: asr=%s audio_ms=%.0f asr_ms=%.0f", session_id, asr.name, audio_ms, asr_ms)
    return build_transcript(session_id, transcript, audio_ms, asr_ms)
