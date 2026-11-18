"""Ollama while Hams is idle: unloading only what Hams loaded, and reaching the local service past a system proxy.

`release_own_models` runs when a game goes full screen and from the tray ("Free memory now"). It must never take a
model away from another program that uses the same Ollama service, and it must never fail loudly: Ollama may not run
at all. The proxy case is checked against a real HTTP server on 127.0.0.1 with a proxy that does not exist.
"""

import http.server
import json
import threading
import urllib.error
import urllib.request

import pytest

from hams import ollama

BASE = "http://127.0.0.1:11434"


class Service:
    """A stand-in for Ollama's HTTP API: `/api/ps` answers with `loaded`, every request is recorded."""

    def __init__(self, loaded=()) -> None:
        self.loaded = list(loaded)
        self.requests: list = []
        self.down = False
        self.unload_error: BaseException | None = None

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = json.loads(request.data) if request.data else None
        self.requests.append((url.removeprefix(BASE), body))
        if self.down:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        if url.endswith("/api/ps"):
            return Answer({"models": [{"name": name, "model": name} for name in self.loaded]})
        if url.endswith("/api/generate"):
            if body.get("keep_alive") == 0 and self.unload_error is not None:
                raise self.unload_error
            return Answer({"response": "ok", "done": True})
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    def unloads(self) -> list:
        return [body["model"] for path, body in self.requests if path == "/api/generate" and body["keep_alive"] == 0]


class Answer:
    def __init__(self, payload) -> None:
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def client_with(service: Service) -> ollama.OllamaClient:
    return ollama.OllamaClient(BASE, opener=service)


class TestOnlyWhatHamsLoaded:
    def test_its_own_model_is_unloaded(self):
        service = Service(loaded=["qwen3.5:9b"])
        client = client_with(service)
        client.warm_up("qwen3.5:9b", "10m")
        assert client.release_own_models() == ["qwen3.5:9b"]
        assert service.unloads() == ["qwen3.5:9b"]
        assert ("/api/generate", {"model": "qwen3.5:9b", "keep_alive": 0}) in service.requests

    def test_a_model_another_program_loaded_stays(self):
        """Hams used qwen; Ollama holds only llama for someone else: nothing to do."""
        service = Service(loaded=["llama3.2:3b"])
        client = client_with(service)
        client.generate("Text", "qwen3.5:9b", timeout=5, keep_alive="10m")
        assert client.release_own_models() == []
        assert service.unloads() == []

    def test_next_to_a_foreign_model_only_its_own_goes(self):
        service = Service(loaded=["llama3.2:3b", "qwen3.5:9b"])
        client = client_with(service)
        client.generate("Text", "qwen3.5:9b", timeout=5, keep_alive="10m")
        assert client.release_own_models() == ["qwen3.5:9b"]
        assert service.unloads() == ["qwen3.5:9b"]

    def test_a_model_hams_never_used_is_never_unloaded(self):
        """The cleanup model of the settings, loaded by another program, is not Hams' to unload."""
        service = Service(loaded=["qwen3.5:9b"])
        client = client_with(service)
        assert client.release_own_models() == []
        assert service.requests == []  # not even asked

    def test_a_name_without_a_tag_is_latest(self):
        service = Service(loaded=["llama3:latest"])
        client = client_with(service)
        client.warm_up("llama3", "10m")
        assert client.release_own_models() == ["llama3:latest"]

    def test_unloaded_once(self):
        service = Service(loaded=["qwen3.5:9b"])
        client = client_with(service)
        client.warm_up("qwen3.5:9b", "10m")
        client.release_own_models()
        service.loaded = []
        assert client.release_own_models() == []
        assert service.unloads() == ["qwen3.5:9b"]


class TestOllamaNotThere:
    def test_unreachable_is_quiet(self):
        service = Service(loaded=["qwen3.5:9b"])
        client = client_with(service)
        client.warm_up("qwen3.5:9b", "10m")
        service.down = True
        assert client.release_own_models() == []

    def test_an_unload_that_fails_is_only_logged(self, caplog):
        service = Service(loaded=["qwen3.5:9b"])
        client = client_with(service)
        client.warm_up("qwen3.5:9b", "10m")
        service.unload_error = urllib.error.HTTPError(BASE, 500, "boom", {}, None)
        assert client.release_own_models() == []
        assert "not unloaded" in caplog.text

    def test_an_old_ollama_without_ps(self):
        """`/api/ps` came later; without it nothing can be told apart, so nothing is unloaded."""
        service = Service()
        client = client_with(service)
        client.warm_up("qwen3.5:9b", "10m")
        service.loaded = None  # makes /api/ps answer 404 below

        def no_ps(request, timeout=None):
            if request.full_url.endswith("/api/ps"):
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            return service(request, timeout)

        client._opener = no_ps
        assert client.release_own_models() == []


class TestLoopback:
    @pytest.mark.parametrize(
        ("url", "local"),
        [
            ("http://127.0.0.1:11434", True),
            ("http://127.1.2.3:11434", True),
            ("http://[::1]:11434", True),
            ("http://localhost:11434", True),
            ("http://LOCALHOST", True),
            ("http://192.168.1.5:11434", False),
            ("http://ollama.lan:11434", False),
            ("http://localhost.example.com", False),
            ("not a url[", False),
        ],
    )
    def test_is_loopback(self, url, local):
        assert ollama.is_loopback(url) is local


class _OllamaLike(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the name http.server calls
        payload = {"models": [{"name": "qwen3.5:9b", "size": 1}]} if self.path == "/api/tags" else {"models": []}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        data = json.dumps({"response": "ok", "done": True}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_service():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OllamaLike)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def broken_proxy(monkeypatch):
    """A system proxy that does not exist, as behind a company proxy with the network gone."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens on the discard port
    # The real opener, not the one conftest routes through `urlopen`.
    monkeypatch.setattr(ollama, "_DIRECT", ollama.DIRECT_OPENER)


class TestPastTheSystemProxy:
    def test_the_proxy_would_have_been_used(self, local_service, broken_proxy):
        """The control: urllib's usual way sends 127.0.0.1 to the proxy and fails."""
        with pytest.raises(OSError):
            urllib.request.build_opener().open(f"{local_service}/api/tags", timeout=5)

    def test_the_local_service_is_reached_directly(self, local_service, broken_proxy):
        client = ollama.OllamaClient(local_service)
        models, error = client.list_models(timeout=5)
        assert error == "" and [model.name for model in models] == ["qwen3.5:9b"]

    def test_a_generate_goes_directly_too(self, local_service, broken_proxy):
        client = ollama.OllamaClient(local_service)
        assert client.generate("Hallo", "qwen3.5:9b", timeout=5, keep_alive="10m") == "ok"

    def test_a_remote_service_still_follows_the_proxy(self, monkeypatch):
        """Only this machine goes around the proxy; another address keeps the system's way."""
        seen = []
        monkeypatch.setattr(ollama, "_DIRECT", ollama.DIRECT_OPENER)
        monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: seen.append(request.full_url))
        ollama.OllamaClient("http://192.168.1.5:11434")._open(
            urllib.request.Request("http://192.168.1.5:11434/api/tags"), 1
        )
        assert seen == ["http://192.168.1.5:11434/api/tags"]
