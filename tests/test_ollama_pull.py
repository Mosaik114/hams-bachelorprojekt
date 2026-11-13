"""`ollama.pull`: der einzige Weg, auf dem Hams ein Ollama-Modell lädt (aus dem Einrichtungsassistenten).

Geprüft an einer Ersatzantwort im Format von `/api/pull`; es wird nie eine Verbindung aufgebaut.
"""

import json
import urllib.error

import pytest

from hams import ollama


class Response:
    def __init__(self, lines):
        self.lines = [line if isinstance(line, bytes) else json.dumps(line).encode() for line in lines]
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


def opener_for(lines, seen=None):
    def opener(request, timeout=None):
        if seen is not None:
            seen.append((request.full_url, json.loads(request.data), timeout))
        return Response(lines)

    return opener


STREAM = [
    {"status": "pulling manifest"},
    {"status": "pulling a1", "digest": "a1", "total": 600, "completed": 0},
    {"status": "pulling a1", "digest": "a1", "total": 600, "completed": 300},
    {"status": "pulling b2", "digest": "b2", "total": 400, "completed": 400},
    {"status": "pulling a1", "digest": "a1", "total": 600, "completed": 600},
    {"status": "verifying sha256 digest"},
    {"status": "success"},
]


class TestPull:
    def test_progress_adds_up_all_layers(self):
        seen, progress = [], []
        ollama.pull(
            "http://127.0.0.1:11434",
            "qwen3.5:9b",
            lambda d, t: progress.append((d, t)),
            lambda: False,
            opener=opener_for(STREAM, seen),
        )
        url, body, timeout = seen[0]
        assert url == "http://127.0.0.1:11434/api/pull"
        assert body == {"model": "qwen3.5:9b", "stream": True}
        assert timeout == ollama.PULL_TIMEOUT
        assert progress == [(0, 600), (300, 600), (700, 1000), (1000, 1000)]

    def test_blank_and_broken_lines_are_skipped(self):
        lines = [b"", b"kein json", b"[1, 2]", {"status": "success"}]
        ollama.pull("http://x", "m", lambda d, t: None, lambda: False, opener=opener_for(lines))

    def test_error_line_becomes_a_sentence(self, english):
        lines = [{"error": "pull model manifest: file does not exist"}]
        with pytest.raises(ollama.PullError, match="couldn't download the model: pull model manifest"):
            ollama.pull("http://x", "m", lambda d, t: None, lambda: False, opener=opener_for(lines))

    def test_stream_without_success_is_incomplete(self, english):
        with pytest.raises(ollama.PullError, match="stopped before"):
            ollama.pull("http://x", "m", lambda d, t: None, lambda: False, opener=opener_for(STREAM[:3]))

    def test_cancel_stops_before_the_next_line(self):
        asked = []

        def cancel():
            asked.append(1)
            return len(asked) > 2

        progress = []
        with pytest.raises(ollama.PullCancelled):
            ollama.pull("http://x", "m", lambda d, t: progress.append(d), cancel, opener=opener_for(STREAM))
        assert progress == [0]

    def test_unreachable_service(self, english):
        def opener(request, timeout=None):
            raise urllib.error.URLError("refused")

        with pytest.raises(ollama.PullError, match="Ollama isn't reachable"):
            ollama.pull("http://x", "m", lambda d, t: None, lambda: False, opener=opener)

    def test_cancelled_is_a_pull_error(self):
        assert issubclass(ollama.PullCancelled, ollama.PullError)
