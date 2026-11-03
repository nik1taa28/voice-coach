import re

MAX_SENTENCE_CHARS = 200  # a reply with no punctuation must still start speaking

# Terminator(s), optional closing quote/bracket, then whitespace. Requiring the whitespace means "3.5" and "e.g." never match
# in the middle, and a "." at the very end of the buffer waits for the next piece (it might be "3." then "5").
_BOUNDARY = re.compile(r"[.?!]+[\"')\]]*(?=\s)")
_ABBREVIATIONS = {"e.g.", "i.e.", "dr.", "mr.", "mrs.", "ms.", "vs.", "approx."}


class SentenceSplitter:
    """Turns streamed text pieces into complete sentences. Pure: no I/O, so it is a plain unit test."""

    def __init__(self, max_chars: int = MAX_SENTENCE_CHARS) -> None:
        self._max_chars = max_chars
        self._buffer = ""

    def feed(self, piece: str) -> list[str]:
        """Add a piece; return the sentences that are now complete (possibly none)."""
        self._buffer += piece
        sentences: list[str] = []
        while (cut := self._find_boundary()) is not None:
            sentence = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:].lstrip()
            if sentence:
                sentences.append(sentence)
        while len(self._buffer) > self._max_chars:
            cut = self._buffer.rfind(" ", 0, self._max_chars)
            if cut <= 0:  # one giant word: hard cut
                cut = self._max_chars
            sentences.append(self._buffer[:cut].strip())
            self._buffer = self._buffer[cut:].lstrip()
        return sentences

    def flush(self) -> list[str]:
        """The stream ended: whatever is left is the last sentence."""
        rest = self._buffer.strip()
        self._buffer = ""
        return [rest] if rest else []

    def _find_boundary(self) -> int | None:
        for match in _BOUNDARY.finditer(self._buffer):
            last_word = self._buffer[: match.end()].split()[-1].lower().rstrip("\"')]")
            if last_word in _ABBREVIATIONS:
                continue
            return match.end()
        return None
