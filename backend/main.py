import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

from backend.asr import ASRError, get_asr_provider
from backend.audio import AudioError, pcm16_duration_ms
from backend.llm import LLMError, get_llm_provider
from backend.prompts import build_messages, build_summary_messages
from backend.session_state import InterviewSession, SessionError, State
from backend.sessions import (
    UtteranceBuffer,
    build_coach_delta,
    build_coach_done,
    build_echo,
    build_error,
    build_state,
    build_summary,
    build_transcript,
    new_session_id,
)

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
    session = InterviewSession()
    await websocket.send_json({"type": "session", "session_id": session_id})
    try:
        llm = get_llm_provider()
    except LLMError as exc:  # e.g. LLM_PROVIDER=gemini without a key: tell the client, then hang up
        logger.error("session %s: LLM unavailable: %s", session_id, exc)
        await websocket.send_json(build_error(session_id, f"LLM unavailable: {exc}"))
        await websocket.close(code=1011)
        return
    logger.info("session %s connected (asr=%s, llm=%s)", session_id, asr.name, llm.name)

    async def send_state() -> None:
        await websocket.send_json(build_state(session_id, session.state.value))

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
            if kind == "start":
                session.start(message.get("topic"), message.get("difficulty"))
                await send_state()
                await coach_turn(websocket, session, llm, session_id)
            elif kind == "pause":
                session.pause()
                utterance.discard()
                await send_state()
            elif kind == "resume":
                session.resume()
                await send_state()
            elif kind == "end":
                session.end()
                utterance.discard()
                await send_state()
                await send_summary(websocket, session, llm, session_id)
            elif kind == "utterance_start":
                session.ensure_can_record()
                utterance.start(message.get("sample_rate"))
            elif kind == "utterance_end":
                session.ensure_can_record()
                finished = utterance.finish()
                if finished is not None:
                    pcm, rate = finished
                    reply = await transcribe_timed(asr, session_id, pcm, rate)
                    await websocket.send_json(reply)
                    if session.state is State.LISTENING:  # an interview is running; idle = transcribe-only
                        if not reply["text"].strip():
                            await websocket.send_json(build_error(session_id, "no speech recognized, please try again"))
                        else:
                            session.add_user(reply["text"])
                            await send_state()
                            await coach_turn(websocket, session, llm, session_id)
            else:
                await websocket.send_json(build_echo(session_id, message))
        except (AudioError, SessionError) as exc:
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


async def stream_timed(llm, messages, on_piece: Callable[[str], Awaitable[None]] | None = None) -> tuple[str, float, float]:
    """Consume an LLM stream; return (full_text, ttft_ms, total_ms). Pieces are forwarded to on_piece as they arrive."""
    started = time.perf_counter()
    first: float | None = None
    parts: list[str] = []
    async for piece in llm.stream(messages):
        if first is None:
            first = time.perf_counter()  # stamped before forwarding, so TTFT excludes our own send time
        parts.append(piece)
        if on_piece is not None:
            await on_piece(piece)
    finished = time.perf_counter()
    if first is None:
        raise LLMError("LLM returned an empty reply")
    return "".join(parts), (first - started) * 1000, (finished - started) * 1000


async def coach_turn(websocket: WebSocket, session: InterviewSession, llm, session_id: str) -> None:
    """Generate the coach's next line (state must be THINKING). Streams deltas; history is written only when complete."""
    messages = build_messages(session.topic, session.difficulty, session.history)

    async def send_delta(piece: str) -> None:
        await websocket.send_json(build_coach_delta(session_id, piece))

    try:
        text, ttft_ms, total_ms = await stream_timed(llm, messages, send_delta)
    except LLMError:
        logger.exception("session %s: LLM failed", session_id)
        session.fail_turn()  # client may hold partial deltas; the error tells it to discard them
        await websocket.send_json(build_error(session_id, "coach reply failed"))
        await websocket.send_json(build_state(session_id, session.state.value))
        return
    text = text.strip()  # what the client shows and what history stores must be identical
    session.add_coach(text)
    logger.info("session %s: llm=%s ttft_ms=%.0f total_ms=%.0f", session_id, llm.name, ttft_ms, total_ms)
    await websocket.send_json(build_coach_done(session_id, text, llm.name, ttft_ms, total_ms))
    await websocket.send_json(build_state(session_id, session.state.value))


async def send_summary(websocket: WebSocket, session: InterviewSession, llm, session_id: str) -> None:
    """One LLM call over the whole history. Not streamed to the client: it is sent once complete."""
    history = session.history
    if not any(m.role == "user" for m in history):
        await websocket.send_json(build_summary(session_id, "No answers were recorded, so there is nothing to summarise.", "none", None, None))
        return
    try:
        text, ttft_ms, total_ms = await stream_timed(llm, build_summary_messages(history))
    except LLMError:
        logger.exception("session %s: summary failed", session_id)
        await websocket.send_json(build_error(session_id, "summary failed"))
        return
    logger.info("session %s: summary llm=%s total_ms=%.0f", session_id, llm.name, total_ms)
    await websocket.send_json(build_summary(session_id, text, llm.name, ttft_ms, total_ms))
