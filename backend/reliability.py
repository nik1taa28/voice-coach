"""Deadlines and the one retry rule. Pure asyncio: no FastAPI, so every rule is a plain unit test."""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, fields
from typing import AsyncIterator, Awaitable, Callable, Mapping, TypeVar

from backend.llm.base import LLMError

logger = logging.getLogger("voice_coach")
T = TypeVar("T")

RETRY_DELAY_S = 0.3


class StageTimeout(RuntimeError):
    """A pipeline stage did not finish inside its deadline. `stage` names it for logs and client messages."""

    def __init__(self, stage: str, seconds: float) -> None:
        super().__init__(f"{stage} timed out after {seconds:g}s")
        self.stage = stage


@dataclass(frozen=True)
class Timeouts:
    """Deadlines in seconds. Starting values are guesses to tune with M5 data, not measurements."""

    asr_s: float = 30.0
    llm_first_s: float = 10.0  # until the first piece arrives (TTFT)
    llm_total_s: float = 30.0  # whole reply, includes our own forwarding and TTS (they run inline)
    tts_s: float = 5.0  # one sentence
    summary_s: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Timeouts":
        """TIMEOUT_ASR_S, TIMEOUT_LLM_FIRST_S, ... A bad value fails loudly instead of silently using the default."""
        env = os.environ if env is None else env
        values: dict[str, float] = {}
        for f in fields(cls):
            raw = env.get(f"TIMEOUT_{f.name.upper()}")
            if raw is None:
                continue
            try:
                seconds = float(raw)
            except ValueError:
                raise ValueError(f"TIMEOUT_{f.name.upper()} must be a number, got {raw!r}") from None
            if not seconds > 0:  # also rejects nan
                raise ValueError(f"TIMEOUT_{f.name.upper()} must be greater than 0, got {raw!r}")
            values[f.name] = seconds
        return cls(**values)


async def with_deadline(awaitable: Awaitable[T], seconds: float, stage: str) -> T:
    """Await with a deadline; raise StageTimeout(stage) on expiry. The awaitable is cancelled when time runs out."""
    deadline = asyncio.timeout(seconds)
    try:
        async with deadline:
            return await awaitable
    except TimeoutError as exc:
        if not deadline.expired():
            raise  # a TimeoutError from inside the awaitable is not our deadline; do not mislabel it
        raise StageTimeout(stage, seconds) from exc


@dataclass(frozen=True)
class StreamResult:
    text: str
    ttft_ms: float  # from the first attempt's start, so a retry's cost shows up in what the user felt
    total_ms: float
    attempts: int


async def stream_with_retry(
    make_stream: Callable[[], AsyncIterator[str]],
    first_s: float,
    total_s: float,
    on_piece: Callable[[str], Awaitable[None]] | None = None,
    retry_delay_s: float = RETRY_DELAY_S,
) -> StreamResult:
    """Consume an LLM stream under two deadlines (first piece, whole reply).

    One retry, and only while NO piece has been produced: nothing has reached the client and no history was written,
    so repeating is safe. After the first piece a retry would repeat text the user already saw, so the error propagates.
    Raises LLMError or StageTimeout. `make_stream` is called once per attempt.
    """
    started = time.perf_counter()
    attempt = 0
    while True:
        attempt += 1
        parts: list[str] = []
        first: float | None = None
        stream = make_stream()
        total_deadline = asyncio.timeout(total_s)
        try:
            try:
                async with total_deadline:
                    iterator = stream.__aiter__()
                    while True:
                        try:
                            if first is None:
                                piece = await with_deadline(iterator.__anext__(), first_s, "llm_first_piece")
                            else:
                                piece = await iterator.__anext__()
                        except StopAsyncIteration:
                            break
                        if first is None:
                            first = time.perf_counter()  # stamped before forwarding, so TTFT excludes our own send time
                        parts.append(piece)
                        if on_piece is not None:
                            await on_piece(piece)
            except TimeoutError as exc:
                if not total_deadline.expired():
                    raise
                raise StageTimeout("llm_total", total_s) from exc
            if first is None:
                raise LLMError("LLM returned an empty reply")
            finished = time.perf_counter()
            return StreamResult("".join(parts), (first - started) * 1000, (finished - started) * 1000, attempt)
        except (LLMError, StageTimeout) as exc:
            if parts or attempt >= 2:
                raise
            logger.warning("LLM attempt %d failed before any output (%s); retrying once", attempt, exc)
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()  # release the HTTP stream now, also when we were cancelled or timed out
        await asyncio.sleep(retry_delay_s)


class TurnRunner:
    """Runs at most one background task per session, so the receive loop stays free to read Pause / End / disconnect."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, coro: Awaitable[None]) -> None:
        if self.active:
            coro.close()  # never awaited: close it so Python does not warn
            raise RuntimeError("a turn is already running")
        self._task = asyncio.create_task(coro)

    async def cancel(self) -> bool:
        """Cancel the running task and wait until it has stopped. True if one was running."""
        task = self._task
        if task is None or task.done():
            return False
        task.cancel()
        await asyncio.wait({task})  # unlike `await task`, this does not swallow OUR OWN cancellation
        return True
