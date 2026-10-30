import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.asr import ASRError, get_asr_provider
from backend.audio import AudioError, pcm16_duration_ms
from backend.llm import LLMError, get_llm_provider
from backend.prompts import build_messages, build_summary_messages
from backend.reliability import StageTimeout, Timeouts, TurnRunner, stream_with_retry, with_deadline
from backend.sentences import SentenceSplitter
from backend.session_state import InterviewSession, SessionError, State
from backend.sessions import (
    UtteranceBuffer,
    build_coach_delta,
    build_coach_done,
    build_echo,
    build_error,
    build_speak,
    build_state,
    build_summary,
    build_transcript,
    new_session_id,
)
from backend.tts import TTSError, get_tts_provider

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


class SafeSender:
    """Every outgoing message goes through here.

    One lock, so a turn being cancelled and a state message cannot interleave. A closed socket returns False instead of
    raising, so a turn that is still running when the client leaves ends quietly (the receive loop cancels it anyway).
    """

    def __init__(self, websocket: WebSocket, session_id: str) -> None:
        self._websocket = websocket
        self._session_id = session_id
        self._lock = asyncio.Lock()
        self._logged_gone = False

    async def __call__(self, message: dict) -> bool:
        async with self._lock:
            ws = self._websocket
            if ws.client_state is not WebSocketState.CONNECTED or ws.application_state is not WebSocketState.CONNECTED:
                return self._gone(message)
            try:
                await ws.send_json(message)
            except WebSocketDisconnect:  # the client left between the check and the send
                return self._gone(message)
        return True

    def _gone(self, message: dict) -> bool:
        if not self._logged_gone:  # once per session, not once per dropped message
            logger.info("session %s: client gone, dropping %s", self._session_id, message.get("type"))
            self._logged_gone = True
        return False


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = new_session_id()
    send = SafeSender(websocket, session_id)
    asr = get_asr_provider()
    tts = get_tts_provider()
    timeouts = Timeouts.from_env()
    utterance = UtteranceBuffer()
    session = InterviewSession()
    turns = TurnRunner()
    await send({"type": "session", "session_id": session_id})
    try:
        llm = get_llm_provider()
    except LLMError as exc:  # e.g. LLM_PROVIDER=gemini without a key: tell the client, then hang up
        logger.error("session %s: LLM unavailable: %s", session_id, exc)
        await send(build_error(session_id, f"LLM unavailable: {exc}"))
        await websocket.close(code=1011)
        return
    logger.info("session %s connected (asr=%s, llm=%s, tts=%s)", session_id, asr.name, llm.name, tts.name)

    async def send_state() -> None:
        await send(build_state(session_id, session.state.value))

    async def interrupt() -> None:
        """Cancel the running turn, if any, and roll back its unanswered message.

        The state check matters: a turn that finished a moment ago is LISTENING and must keep its reply. A turn cancelled
        during ASR is also LISTENING (nothing was added yet), so there is nothing to roll back either.
        """
        was_running = await turns.cancel()
        if session.state is State.THINKING:
            session.cancel_turn()
        if was_running:
            logger.info("session %s: turn cancelled, state now %s", session_id, session.state.value)
            await send(build_error(session_id, "turn cancelled"))  # the client already discards partial text + queued speech on any error

    async def interrupt_then(event: Callable[[], None]) -> None:
        """Pause / End: stop the running turn first, then apply the event."""
        before = session.state
        if before is not State.ENDED:  # in ENDED the only task is the summary; pause/end are rejected without killing it
            await interrupt()
        try:
            event()
        except SessionError:
            if session.state is not before:  # the interrupt rolled the state back (e.g. thinking -> idle); the client must hear it
                await send_state()
            raise
        utterance.discard()
        await send_state()

    def ensure_not_busy() -> None:
        if turns.active:
            raise SessionError("busy: the previous request is still being processed")

    async def guarded(coro, what: str) -> None:
        """Task boundary. Nothing awaits this task's exception, so an unexpected one would vanish: log it with the traceback
        and tell the client. CancelledError is a BaseException and passes straight through."""
        try:
            await coro
        except Exception:
            logger.exception("session %s: unexpected error in %s", session_id, what)
            if session.state is State.THINKING:
                session.cancel_turn()
            await send(build_error(session_id, "internal error"))
            await send_state()

    def spawn(what: str, coro) -> None:
        turns.start(guarded(coro, what))

    async def answer_turn(pcm: bytes, rate: int) -> None:
        try:
            reply = await with_deadline(transcribe_timed(asr, session_id, pcm, rate), timeouts.asr_s, "asr")
        except ASRError:
            logger.exception("session %s: ASR failed", session_id)
            await send(build_error(session_id, "transcription failed"))
            return
        except StageTimeout as exc:
            logger.error("session %s: %s", session_id, exc)  # note: a Whisper worker thread cannot be killed and keeps running
            await send(build_error(session_id, "transcription timed out"))
            return
        await send(reply)
        if session.state is State.LISTENING:  # an interview is running; idle = transcribe-only
            if not reply["text"].strip():
                await send(build_error(session_id, "no speech recognized, please try again"))
            else:
                session.add_user(reply["text"])
                await send_state()
                await coach_turn(send, session, llm, tts, session_id, timeouts)

    try:
        while True:
            incoming = await websocket.receive()
            if incoming["type"] == "websocket.disconnect":
                break

            if incoming.get("bytes") is not None:
                try:
                    utterance.add(incoming["bytes"])
                except AudioError as exc:
                    await send(build_error(session_id, str(exc)))
                continue

            try:
                message = json.loads(incoming["text"])
            except json.JSONDecodeError:
                await send(build_error(session_id, "invalid JSON"))
                continue
            if not isinstance(message, dict):
                await send(build_error(session_id, "JSON must be an object"))
                continue

            kind = message.get("type")
            try:
                if kind == "start":
                    ensure_not_busy()
                    session.start(message.get("topic"), message.get("difficulty"))
                    await send_state()
                    spawn("opening question", coach_turn(send, session, llm, tts, session_id, timeouts))
                elif kind == "pause":
                    await interrupt_then(session.pause)
                elif kind == "resume":
                    session.resume()
                    await send_state()
                elif kind == "end":
                    await interrupt_then(session.end)
                    spawn("summary", send_summary(send, session, llm, session_id, timeouts))
                elif kind == "utterance_start":
                    session.ensure_can_record()
                    ensure_not_busy()
                    utterance.start(message.get("sample_rate"))
                elif kind == "utterance_end":
                    session.ensure_can_record()
                    ensure_not_busy()
                    finished = utterance.finish()
                    if finished is not None:
                        pcm, rate = finished
                        spawn("answer", answer_turn(pcm, rate))
                elif kind == "playback_started":
                    # Measured by the browser's own clock (stop press -> first audible sound). Logged as reported, never mixed
                    # with server timings: the two clocks are not comparable.
                    value = message.get("stop_to_audio_ms")
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 600_000:
                        await send(build_error(session_id, "stop_to_audio_ms must be a number between 0 and 600000"))
                    else:
                        logger.info("session %s: browser stop_to_audio_ms=%.0f", session_id, value)
                else:
                    await send(build_echo(session_id, message))
            except (AudioError, SessionError) as exc:
                await send(build_error(session_id, str(exc)))
    finally:
        # Runs on disconnect, and also if this handler itself fails or is cancelled: no orphaned turn keeps calling providers.
        await turns.cancel()
        utterance.discard()
        logger.info("session %s disconnected", session_id)


async def transcribe_timed(asr, session_id: str, pcm: bytes, sample_rate: int) -> dict:
    """Run ASR and time only that stage, with a monotonic clock."""
    audio_ms = pcm16_duration_ms(pcm, sample_rate)
    started = time.perf_counter()
    transcript = await asr.transcribe(pcm, sample_rate)
    asr_ms = (time.perf_counter() - started) * 1000
    logger.info("session %s: asr=%s audio_ms=%.0f asr_ms=%.0f", session_id, asr.name, audio_ms, asr_ms)
    return build_transcript(session_id, transcript, audio_ms, asr_ms)


async def coach_turn(send: SafeSender, session: InterviewSession, llm, tts, session_id: str, timeouts: Timeouts) -> None:
    """Generate the coach's next line (state must be THINKING). Streams deltas; history is written only when complete.

    Each complete sentence is also sent to TTS and out as a `speak` message while the LLM keeps writing. TTS runs inline
    in the stream loop (no queue yet): a slow server-side TTS delays reading the next LLM piece, and llm_total_ms includes it.
    Failures: the LLM is retried once before its first piece (see stream_with_retry); TTS is never retried.
    """
    messages = build_messages(session.topic, session.difficulty, session.history)
    splitter = SentenceSplitter()
    turn_started = time.perf_counter()
    next_seq = 0
    first_sentence_ms: float | None = None
    first_speak_ms: float | None = None
    speech_failed = False  # after one TTS failure the rest of the reply is text only, and the client hears about it once

    async def speak_sentences(sentences: list[str]) -> None:
        nonlocal next_seq, first_sentence_ms, first_speak_ms, speech_failed
        for sentence in sentences:
            if first_sentence_ms is None:
                first_sentence_ms = (time.perf_counter() - turn_started) * 1000
            if speech_failed:
                continue
            started = time.perf_counter()
            try:
                chunk = await with_deadline(tts.synthesize(sentence, next_seq), timeouts.tts_s, "tts")
            except (TTSError, StageTimeout):
                logger.exception("session %s: TTS failed on sentence %d", session_id, next_seq)
                speech_failed = True
                await send(build_error(session_id, "speech failed"))
                continue
            tts_ms = None if chunk.audio is None else (time.perf_counter() - started) * 1000  # browser speech: nothing to time
            await send(build_speak(session_id, chunk, tts.name, tts_ms))
            if first_speak_ms is None:
                first_speak_ms = (time.perf_counter() - turn_started) * 1000
            next_seq += 1

    async def on_piece(piece: str) -> None:
        await send(build_coach_delta(session_id, piece))
        await speak_sentences(splitter.feed(piece))

    try:
        result = await stream_with_retry(lambda: llm.stream(messages), timeouts.llm_first_s, timeouts.llm_total_s, on_piece)
        await speak_sentences(splitter.flush())  # the unterminated tail of the reply
    except (LLMError, StageTimeout) as exc:
        logger.exception("session %s: LLM failed", session_id)
        session.fail_turn()  # client may hold partial deltas and queued speech; the error tells it to discard both
        reason = "coach reply timed out" if isinstance(exc, StageTimeout) else "coach reply failed"
        await send(build_error(session_id, reason))
        await send(build_state(session_id, session.state.value))
        return
    text = result.text.strip()  # what the client shows and what history stores must be identical
    session.add_coach(text)
    logger.info(
        "session %s: llm=%s attempts=%d ttft_ms=%.0f total_ms=%.0f tts=%s first_sentence_ms=%s first_speak_ms=%s",
        session_id, llm.name, result.attempts, result.ttft_ms, result.total_ms, tts.name,
        None if first_sentence_ms is None else round(first_sentence_ms), None if first_speak_ms is None else round(first_speak_ms),
    )
    await send(build_coach_done(session_id, text, llm.name, result.ttft_ms, result.total_ms, tts.name, first_sentence_ms, first_speak_ms))
    await send(build_state(session_id, session.state.value))


async def send_summary(send: SafeSender, session: InterviewSession, llm, session_id: str, timeouts: Timeouts) -> None:
    """One LLM call over the whole history. Not streamed to the client: it is sent once complete. Same retry rule as a reply."""
    history = session.history
    if not any(m.role == "user" for m in history):
        await send(build_summary(session_id, "No answers were recorded, so there is nothing to summarise.", "none", None, None))
        return
    try:
        result = await stream_with_retry(lambda: llm.stream(build_summary_messages(history)), timeouts.llm_first_s, timeouts.summary_s)
    except (LLMError, StageTimeout) as exc:
        logger.exception("session %s: summary failed", session_id)
        await send(build_error(session_id, "summary timed out" if isinstance(exc, StageTimeout) else "summary failed"))
        return
    logger.info("session %s: summary llm=%s attempts=%d total_ms=%.0f", session_id, llm.name, result.attempts, result.total_ms)
    await send(build_summary(session_id, result.text, llm.name, result.ttft_ms, result.total_ms))
