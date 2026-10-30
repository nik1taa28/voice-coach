from backend.tts.base import SpokenChunk


class BrowserTTS:
    """The server does no synthesis: it forwards the sentence and the browser's speechSynthesis speaks it.

    So the server cannot time synthesis or playback; the browser reports when audio actually started.
    """

    name = "browser"

    async def synthesize(self, text: str, seq: int) -> SpokenChunk:
        return SpokenChunk(seq, text, None, None)
