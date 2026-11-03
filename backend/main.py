import asyncio
import dataclasses
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.asr import ASRError, get_asr_provider
from backend.audio import AudioError, pcm16_duration_ms
from backend.decision import FALLBACK_ACK, Decision, DecisionError, parse_decision
from backend.delivery import analyze, session_line
from backend.llm import LLMError, get_llm_provider
from backend.logs import configure_logging, session_id_var
from backend.metrics import Metrics
from backend.prompts import build_decision_messages, build_summary_messages
from backend.questions import followups_per_question, load_bank, topics
from backend.reliability import StageTimeout, StreamResult, Timeouts, TurnRunner, stream_with_retry, with_deadline
from backend.sentences import SentenceSplitter
from backend.questions import DIFFICULTIES
from backend.session_state import DEFAULT_QUESTION_COUNT, DEFAULT_TIME_LIMIT_S, EndReason, InterviewSession, SessionError, State, TurnPlan
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

configure_logging()
logger = logging.getLogger("voice_coach")
app = FastAPI(title="Voice Coach")
metrics = Metrics.from_env()  # one per process; looked up at call time, so tests can swap it with monkeypatch
question_bank = load_bank()  # loaded once at startup: a bad bank file stops the server here, not in the middle of an interview
followups = followups_per_question()  # cap on LLM follow-ups per bank question (FOLLOWUPS_PER_QUESTION, default 2)
question_rng = random.Random()  # tests swap it for a seeded one


def interview_time_limit_s() -> float:
    """INTERVIEW_TIME_LIMIT_S (default 900). Read once at startup like the other settings; a bad value fails loudly."""
    raw = os.environ.get("INTERVIEW_TIME_LIMIT_S")
    if raw is None:
        return DEFAULT_TIME_LIMIT_S
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"INTERVIEW_TIME_LIMIT_S must be a number, got {raw!r}") from None
    if not value > 0:  # also rejects nan
        raise ValueError(f"INTERVIEW_TIME_LIMIT_S must be greater than 0, got {raw!r}")
    return value


time_limit_s = interview_time_limit_s()
CLOSING_TEXT = {
    EndReason.QUESTION_LIMIT: "That was the last question. Thank you, here is your summary.",
    EndReason.TIME_LIMIT: "We are out of time. Thank you, here is your summary.",
    EndReason.BANK_EXHAUSTED: "That was the last question for this topic. Here is your summary.",
    EndReason.MANUAL: "Thanks for practising. Here is your summary.",
}


INDEX_HTML = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
DASHBOARD_HTML = INDEX_HTML.with_name("dashboard.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.get("/dashboard")
async def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_HTML)


@app.get("/topics")
async def topics_endpoint() -> dict:
    return {"topics": topics(question_bank), "difficulties": list(DIFFICULTIES)}


@app.get("/metrics")
async def metrics_endpoint() -> dict:
    # Open, no auth: fine for a local project, not for a public deployment.
    return metrics.snapshot()


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
            logger.info("client gone, dropping %s", message.get("type"))
            self._logged_gone = True
        return False


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = new_session_id()
    session_id_var.set(session_id)  # every log line from this connection's tasks now carries the id
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
        logger.error("LLM unavailable: %s", exc)
        await send(build_error(session_id, f"LLM unavailable: {exc}"))
        await websocket.close(code=1011)
        return
    logger.info("connected", extra={"fields": {"asr": asr.name, "llm": llm.name, "tts": tts.name}})

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
            metrics.count("turn_cancelled")
            logger.info("turn cancelled", extra={"fields": {"state": session.state.value}})
            await send(build_error(session_id, "turn cancelled"))  # the client already discards partial text + queued speech on any error

    async def interrupt_then(event: Callable[[], None]) -> None:
        """Pause / End: stop the running turn first, then apply the event."""
        before = session.state
        if before not in (State.ENDING, State.ENDED):  # while ending, the only task is the summary; pause/end are rejected without killing it
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
            logger.exception("unexpected error in %s", what)
            metrics.count("turn_failed")
            if session.state is State.THINKING:
                session.cancel_turn()
            await send(build_error(session_id, "internal error"))
            await send_state()

    def spawn(what: str, coro) -> None:
        turns.start(guarded(coro, what))

    async def time_limit_watchdog() -> None:
        """Ends the interview when its time is up, even if the candidate never answers again. The limit is hard: a running
        turn is cancelled and rolled back first, exactly like End. A turn that already ended the interview leaves nothing to do."""
        await asyncio.sleep(max(0.0, session.started_at + session.time_limit_s - time.monotonic()))
        if session.state not in (State.LISTENING, State.PAUSED, State.THINKING):
            return
        try:
            await interrupt_then(lambda: session.begin_ending(EndReason.TIME_LIMIT))
        except SessionError:  # End pressed meanwhile, or the opener was cancelled back to idle: nothing left to end
            return
        logger.info("time limit reached", extra={"fields": {"time_limit_s": session.time_limit_s}})
        spawn("time limit ending", closing_turn(send, session, llm, tts, session_id, timeouts))

    watchdog: asyncio.Task | None = None

    async def answer_turn(pcm: bytes, rate: int) -> None:
        try:
            reply = await with_deadline(transcribe_timed(asr, session_id, pcm, rate), timeouts.asr_s, "asr")
        except ASRError:
            logger.exception("ASR failed")
            metrics.count("turn_failed")
            await send(build_error(session_id, "transcription failed"))
            return
        except StageTimeout as exc:
            logger.error("%s", exc)  # note: a Whisper worker thread cannot be killed and keeps running
            metrics.count("turn_timeout")
            await send(build_error(session_id, "transcription timed out"))
            return
        # Measured from the audio and the transcript; in a thread so a long answer never blocks the event loop
        delivery = await asyncio.to_thread(analyze, reply["text"], pcm, rate)
        reply["delivery"] = dataclasses.asdict(delivery)
        await send(reply)
        if session.state is State.LISTENING:  # an interview is running; idle = transcribe-only
            if not reply["text"].strip():
                await send(build_error(session_id, "no speech recognized, please try again"))
            else:
                session.add_user(reply["text"], delivery)
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
                    session.start(
                        message.get("topic"), message.get("difficulty"), topics(question_bank),
                        message.get("question_count", DEFAULT_QUESTION_COUNT), time_limit_s,
                    )
                    await send_state()
                    watchdog = asyncio.create_task(guarded(time_limit_watchdog(), "time limit"))
                    spawn("opening question", coach_turn(send, session, llm, tts, session_id, timeouts, opening=True))
                elif kind == "pause":
                    await interrupt_then(session.pause)
                elif kind == "resume":
                    session.resume()
                    await send_state()
                elif kind == "end":
                    await interrupt_then(lambda: session.begin_ending(EndReason.MANUAL))
                    spawn("ending", closing_turn(send, session, llm, tts, session_id, timeouts))
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
                        logger.info("browser playback started", extra={"fields": {"stop_to_audio_ms": round(value)}})
                else:
                    await send(build_echo(session_id, message))
            except (AudioError, SessionError) as exc:
                await send(build_error(session_id, str(exc)))
    finally:
        # Runs on disconnect, and also if this handler itself fails or is cancelled: no orphaned turn keeps calling providers.
        if watchdog is not None:
            watchdog.cancel()  # not awaited: suppressing its CancelledError here could also swallow a cancel of this handler
        await turns.cancel()
        utterance.discard()
        logger.info("disconnected")


async def transcribe_timed(asr, session_id: str, pcm: bytes, sample_rate: int) -> dict:
    """Run ASR and time only that stage, with a monotonic clock."""
    audio_ms = pcm16_duration_ms(pcm, sample_rate)
    started = time.perf_counter()
    transcript = await asr.transcribe(pcm, sample_rate)
    asr_ms = (time.perf_counter() - started) * 1000
    metrics.observe("asr", asr_ms)
    logger.info("asr done", extra={"fields": {"asr": asr.name, "audio_ms": round(audio_ms), "asr_ms": round(asr_ms)}})
    return build_transcript(session_id, transcript, audio_ms, asr_ms)


class Speaker:
    """Sends one coach line as text, then as speech one sentence at a time, and times both from the turn start.

    The line is complete before it is sent (a bank line, or an LLM decision that had to be validated first), so it goes
    out as a single delta: since the JSON decision, follow-ups are NOT streamed to the client.
    """

    def __init__(self, send: SafeSender, tts, session_id: str, timeouts: Timeouts) -> None:
        self._send = send
        self._tts = tts
        self._session_id = session_id
        self._timeouts = timeouts
        self._started = time.perf_counter()
        self._seq = 0
        self._failed = False  # after one TTS failure the rest is text only, and the client hears about it once
        self.first_sentence_ms: float | None = None
        self.first_speak_ms: float | None = None

    def _since_start(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    async def say(self, text: str) -> None:
        await self._send(build_coach_delta(self._session_id, text))
        splitter = SentenceSplitter()
        for sentence in splitter.feed(text) + splitter.flush():
            if self.first_sentence_ms is None:
                self.first_sentence_ms = self._since_start()
            if self._failed:
                continue
            started = time.perf_counter()
            try:
                chunk = await with_deadline(self._tts.synthesize(sentence, self._seq), self._timeouts.tts_s, "tts")
            except (TTSError, StageTimeout):
                logger.exception("TTS failed on sentence %d", self._seq)
                self._failed = True
                metrics.count("speech_failed")
                await self._send(build_error(self._session_id, "speech failed"))
                continue
            tts_ms = None if chunk.audio is None else (time.perf_counter() - started) * 1000  # browser speech: nothing to time
            if tts_ms is not None:
                metrics.observe("tts", tts_ms)
            await self._send(build_speak(self._session_id, chunk, self._tts.name, tts_ms))
            if self.first_speak_ms is None:
                self.first_speak_ms = self._since_start()
            self._seq += 1


async def decide(llm, session: InterviewSession, timeouts: Timeouts) -> tuple[Decision, StreamResult | None, str | None]:
    """Ask the LLM follow_up or move_on about the current bank question.

    Returns (decision, LLM result or None, fallback reason or None). A provider problem never fails the turn: a timeout,
    an LLMError or a reply that fails parse_decision all become move_on with FALLBACK_ACK. The usual retry rule applies
    (one retry before the first piece). Cancellation (Pause / End / disconnect) is not caught and passes straight through.
    """
    fallback = Decision("move_on", FALLBACK_ACK)
    messages = build_decision_messages(session.topic, session.history, session.current)
    try:
        result = await stream_with_retry(lambda: llm.stream(messages), timeouts.llm_first_s, timeouts.llm_total_s)
    except StageTimeout as exc:
        logger.error("decision: %s", exc)
        return fallback, None, "timeout"
    except LLMError:
        logger.exception("decision: LLM failed")
        return fallback, None, "error"
    try:
        return parse_decision(result.text, session.current.key_points), result, None
    except DecisionError as exc:
        logger.warning("decision: invalid reply (%s)", exc, extra={"fields": {"raw": result.text[:200]}})
        return fallback, result, "invalid"


async def coach_turn(
    send: SafeSender, session: InterviewSession, llm, tts, session_id: str, timeouts: Timeouts, opening: bool = False
) -> None:
    """The coach's next line (state must be THINKING). The code owns the loop; the LLM only picks follow_up or move_on.

    Opening: the first bank question, no LLM call. After an answer: while follow-ups are left and time is not up, the LLM
    decides (see decide); otherwise the code moves on without asking it. move_on says an acknowledgement plus the next
    bank question, or the closing line if an end condition holds (then the session goes to ENDING and the summary follows
    in this same task). The plan is committed only by add_coach, so a cancelled turn leaves the session unchanged.
    """
    speaker = Speaker(send, tts, session_id, timeouts)
    result: StreamResult | None = None
    fallback: str | None = None
    if opening:
        plan = session.plan_opening(question_bank, question_rng)
        text = plan.question.question if plan.kind == "bank" else CLOSING_TEXT[plan.end_reason]
    else:
        if session.needs_decision(followups, time.monotonic()):
            decision, result, fallback = await decide(llm, session, timeouts)
        else:
            decision = Decision("move_on", FALLBACK_ACK)  # cap reached or time up: the model is not asked
        if decision.action == "follow_up":
            plan, text = TurnPlan("followup", session.current), decision.text
        else:
            plan = session.plan_move_on(question_bank, question_rng, time.monotonic())
            then = plan.question.question if plan.kind == "bank" else CLOSING_TEXT[plan.end_reason]
            text = f"{decision.text} {then}"
    await speaker.say(text)
    session.add_coach(text, plan)
    if result is not None:  # no LLM call (bank line, or code moved on) means no LLM timing; zeros would be invented numbers
        metrics.observe("llm_ttft", result.ttft_ms)
        metrics.observe("llm_total", result.total_ms)
    if speaker.first_sentence_ms is not None:
        metrics.observe("first_sentence", speaker.first_sentence_ms)
    if speaker.first_speak_ms is not None:
        metrics.observe("first_speak", speaker.first_speak_ms)
    if fallback is not None:
        metrics.count("decision_fallback")
    metrics.count("turn_ok")  # a fallback still gave the candidate a reply; decision_fallback shows the LLM problem
    provider_name = "fallback" if fallback is not None else ("bank" if result is None else llm.name)
    logger.info("coach turn done", extra={"fields": {
        "llm": provider_name, "tts": tts.name, "plan": plan.kind, "fallback": fallback,
        "end_reason": None if plan.end_reason is None else plan.end_reason.value,
        "attempts": None if result is None else result.attempts,
        "ttft_ms": None if result is None else round(result.ttft_ms), "total_ms": None if result is None else round(result.total_ms),
        "first_sentence_ms": None if speaker.first_sentence_ms is None else round(speaker.first_sentence_ms),
        "first_speak_ms": None if speaker.first_speak_ms is None else round(speaker.first_speak_ms),
    }})
    await send(build_coach_done(
        session_id, text, provider_name,
        None if result is None else result.ttft_ms, None if result is None else result.total_ms,
        tts.name, speaker.first_sentence_ms, speaker.first_speak_ms,
    ))
    await send(build_state(session_id, session.state.value))
    if session.state is State.ENDING:
        await finish_interview(send, session, llm, session_id, timeouts)


async def closing_turn(send: SafeSender, session: InterviewSession, llm, tts, session_id: str, timeouts: Timeouts) -> None:
    """End pressed or the time limit fired between turns (state ENDING): say the closing line, then the summary.
    The line is not added to the history: it is not part of the interview the summary judges."""
    speaker = Speaker(send, tts, session_id, timeouts)
    text = CLOSING_TEXT[session.end_reason]
    await speaker.say(text)
    await send(build_coach_done(session_id, text, "bank", None, None, tts.name, speaker.first_sentence_ms, speaker.first_speak_ms))
    await finish_interview(send, session, llm, session_id, timeouts)


async def finish_interview(send: SafeSender, session: InterviewSession, llm, session_id: str, timeouts: Timeouts) -> None:
    """ENDING -> ENDED: the summary (or its error), then the session is closed."""
    await send_summary(send, session, llm, session_id, timeouts)
    session.finish()
    await send(build_state(session_id, session.state.value))


async def send_summary(send: SafeSender, session: InterviewSession, llm, session_id: str, timeouts: Timeouts) -> None:
    """One LLM call over the whole history. Not streamed to the client: it is sent once complete. Same retry rule as a reply."""
    history = session.history
    end_reason = session.end_reason.value
    delivery = session_line(session.delivery_stats)  # written by code from measurements; the LLM never sees or states these
    if not any(m.role == "user" for m in history):
        await send(build_summary(session_id, "No answers were recorded, so there is nothing to summarise.", "none", None, None, end_reason, delivery))
        return
    try:
        result = await stream_with_retry(lambda: llm.stream(build_summary_messages(session.topic, session.difficulty, history, session.asked_questions)), timeouts.llm_first_s, timeouts.summary_s)
    except (LLMError, StageTimeout) as exc:
        logger.exception("summary failed")
        await send(build_error(session_id, "summary timed out" if isinstance(exc, StageTimeout) else "summary failed"))
        return
    logger.info("summary done", extra={"fields": {"llm": llm.name, "attempts": result.attempts, "total_ms": round(result.total_ms)}})
    await send(build_summary(session_id, result.text, llm.name, result.ttft_ms, result.total_ms, end_reason, delivery))
