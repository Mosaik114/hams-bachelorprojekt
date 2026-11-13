"""Tests der Ollama-Auskunft und der beiden Modellauswahlen.

Der Dienst wird hier nicht angesprochen: `urlopen` wird ersetzt, damit auch
Zeitüberschreitung, kaputte Antwort und fehlendes `/api/ps` prüfbar sind. Der
Lauf gegen das echte Ollama lief als Integrationstest.
"""

import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from hams import ollama
from hams.settings_window import SettingsWindow as SW

TAGS = {
    "models": [
        {
            "name": "ministral-3:8b",
            "model": "ministral-3:8b",
            "size": 6_022_236_616,
            "details": {"parameter_size": "8.9B", "family": "mistral3", "quantization_level": "Q4_K_M"},
        },
        {
            "name": "qwen3.5:9b",
            "model": "qwen3.5:9b",
            "size": 6_594_474_711,
            "details": {"parameter_size": "9.7B", "family": "qwen35"},
        },
        {
            "name": "qwen2.5:3b",
            "model": "qwen2.5:3b",
            "size": 1_929_912_432,
            "details": {"parameter_size": "3.1B", "family": "qwen2"},
        },
    ]
}
PS_ONE = {"models": [{"name": "ministral-3:8b", "size_vram": 5_642_995_628}]}
PS_TWO = {"models": [{"name": "ministral-3:8b"}, {"name": "qwen2.5:3b"}]}


class _Response:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def fake_urlopen(monkeypatch, routes):
    """`routes`: Pfadende -> Nutzlast, Ausnahme oder roher Text."""
    calls = []

    def _open(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        calls.append(url)
        for suffix, result in routes.items():
            if url.endswith(suffix):
                if isinstance(result, BaseException):
                    raise result
                if isinstance(result, bytes):
                    return _Raw(result)
                return _Response(result)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    return calls


class _Raw:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


BASE = "http://localhost:11434"


# ------------------------------------------------------------------- Adresse


class TestBaseUrl:
    @pytest.mark.parametrize(
        "configured,expected",
        [
            ("http://localhost:11434/api/generate", "http://localhost:11434"),
            ("http://127.0.0.1:11434/api/chat", "http://127.0.0.1:11434"),
            ("http://box:11434", "http://box:11434"),
            ("http://box:11434/", "http://box:11434"),
        ],
    )
    def test_service_address_is_derived(self, configured, expected):
        assert ollama.base_url(configured) == expected

    def test_empty_configuration_does_not_raise(self):
        assert ollama.base_url("") == ""


# -------------------------------------------------------------------- /api/tags


class TestListModels:
    def test_several_models(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})
        models, error = ollama.OllamaClient(BASE).list_models()
        assert error == ""
        assert [m.name for m in models] == ["ministral-3:8b", "qwen3.5:9b", "qwen2.5:3b"]

    def test_empty_list_is_not_an_error(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": {"models": []}})
        models, error = ollama.OllamaClient(BASE).list_models()
        assert models == () and error == ""

    def test_timeout_is_reported_in_plain_words(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TimeoutError("timed out")})
        models, error = ollama.OllamaClient(BASE).list_models()
        assert models == () and error == "Ollama nicht erreichbar"

    def test_service_down(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": ConnectionRefusedError("refused")})
        _models, error = ollama.OllamaClient(BASE).list_models()
        assert error == "Ollama nicht erreichbar"

    def test_http_error_names_the_code(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": urllib.error.HTTPError(BASE, 500, "boom", {}, None)})
        _models, error = ollama.OllamaClient(BASE).list_models()
        assert "500" in error

    def test_broken_answer(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": b"<html>kein json</html>"})
        models, error = ollama.OllamaClient(BASE).list_models()
        assert models == () and "unverständlich" in error

    def test_entries_without_a_name_are_skipped(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": {"models": [{"size": 1}, "quatsch"]}})
        models, error = ollama.OllamaClient(BASE).list_models()
        assert models == () and error == ""

    def test_missing_details_are_left_out(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": {"models": [{"name": "nackt:1b"}]}})
        models, _error = ollama.OllamaClient(BASE).list_models()
        assert models[0].meta == "", "nichts erfinden, was die API nicht liefert"


class TestMeta:
    def test_parameters_and_size(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})
        models, _error = ollama.OllamaClient(BASE).list_models()
        assert models[0].meta == "8,9B · 6,0 GB"

    def test_only_a_size(self):
        assert ollama.OllamaModel("x", 1_929_912_432).meta == "1,9 GB"

    def test_only_parameters(self):
        assert ollama.OllamaModel("x", 0, "7.6B").meta == "7,6B"


# --------------------------------------------------------------------- /api/ps


class TestRunningModels:
    def test_one_warm_model(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/ps": PS_ONE})
        assert ollama.OllamaClient(BASE).running_models() == frozenset({"ministral-3:8b"})

    def test_several_warm_models(self, monkeypatch):
        """keep_alive haelt durchaus mehrere Modelle gleichzeitig im Speicher."""
        fake_urlopen(monkeypatch, {"/api/ps": PS_TWO})
        assert ollama.OllamaClient(BASE).running_models() == frozenset({"ministral-3:8b", "qwen2.5:3b"})

    def test_missing_endpoint_is_tolerated(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})  # /api/ps -> 404
        assert ollama.OllamaClient(BASE).running_models() == frozenset()

    def test_nothing_warm(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/ps": {"models": []}})
        assert ollama.OllamaClient(BASE).running_models() == frozenset()

    @pytest.mark.parametrize("answer", [[1, 2], "text", 42, None, {"models": "x"}, {"models": {"a": 1}}])
    def test_an_answer_of_another_shape_is_nothing_warm(self, monkeypatch, answer):
        """Regression: a list instead of an object raised AttributeError outside the try."""
        fake_urlopen(monkeypatch, {"/api/ps": answer})
        assert ollama.OllamaClient(BASE).running_models() == frozenset()


class TestProbe:
    def test_online_with_warm_model(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS, "/api/ps": PS_ONE})
        state = ollama.OllamaClient(BASE).probe()
        assert state.online and len(state.models) == 3
        assert state.warm == frozenset({"ministral-3:8b"})

    def test_online_without_ps(self, monkeypatch):
        """Ohne /api/ps muss die Auswahl trotzdem funktionieren."""
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})
        state = ollama.OllamaClient(BASE).probe()
        assert state.online and state.warm == frozenset()
        assert len(state.models) == 3

    def test_a_strange_ps_answer_keeps_the_service_online(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS, "/api/ps": ["unexpected"]})
        state = ollama.OllamaClient(BASE).probe()
        assert state.online and state.warm == frozenset()

    def test_tags_with_a_strange_models_field(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": {"models": 7}})
        assert ollama.OllamaClient(BASE).probe().models == ()

    def test_probe_never_raises(self, monkeypatch):
        def broken(client, timeout):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(ollama.OllamaClient, "list_models", broken)
        state = ollama.OllamaClient(BASE).probe()
        assert not state.online and state.error

    def test_offline_does_not_ask_for_ps(self, monkeypatch):
        calls = fake_urlopen(monkeypatch, {"/api/tags": TimeoutError("x")})
        state = ollama.OllamaClient(BASE).probe()
        assert not state.online and state.error
        assert not any("/api/ps" in url for url in calls)

    def test_find_and_names(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS, "/api/ps": PS_ONE})
        state = ollama.OllamaClient(BASE).probe()
        assert state.find("qwen3.5:9b").parameter_size == "9.7B"
        assert state.find("gibtsnicht") is None
        assert "qwen2.5:3b" in state.names


class TestSignature:
    def _state(self, monkeypatch, tags, ps):
        fake_urlopen(monkeypatch, {"/api/tags": tags, "/api/ps": ps})
        return ollama.OllamaClient(BASE).probe()

    def test_same_answer_gives_the_same_signature(self, monkeypatch):
        first = self._state(monkeypatch, TAGS, PS_ONE)
        second = self._state(monkeypatch, TAGS, PS_ONE)
        assert first.signature() == second.signature()
        assert first == second

    def test_a_new_model_changes_it(self, monkeypatch):
        first = self._state(monkeypatch, TAGS, PS_ONE)
        more = {"models": TAGS["models"] + [{"name": "neu:1b"}]}
        assert self._state(monkeypatch, more, PS_ONE).signature() != first.signature()

    def test_a_warm_model_changes_it(self, monkeypatch):
        first = self._state(monkeypatch, TAGS, PS_ONE)
        assert self._state(monkeypatch, TAGS, PS_TWO).signature() != first.signature()

    def test_going_offline_changes_it(self, monkeypatch):
        first = self._state(monkeypatch, TAGS, PS_ONE)
        fake_urlopen(monkeypatch, {"/api/tags": TimeoutError("x")})
        assert ollama.OllamaClient(BASE).probe().signature() != first.signature()


# ----------------------------------------------------------- Auswahllisten


def state_with(names=("a:1b", "b:2b"), warm=(), online=True):
    models = [ollama.OllamaModel(name, 1_000_000_000, "7.6B") for name in names]
    return ollama.OllamaState(online, models, warm, "" if online else "Ollama nicht erreichbar")


class TestOllamaOptions:
    def test_installed_models_are_listed(self):
        options = SW.ollama_options(state_with(), "a:1b")
        assert [o.value for o in options if o.enabled] == ["a:1b", "b:2b"]

    def test_every_entry_has_a_group(self):
        options = SW.ollama_options(state_with(), "a:1b")
        assert all(o.group == "Installierte Modelle" for o in options if o.enabled)

    def test_meta_comes_from_the_api(self):
        options = SW.ollama_options(state_with(), "a:1b")
        assert options[0].meta == "7,6B · 1,0 GB"

    def test_warm_model_is_marked(self):
        options = SW.ollama_options(state_with(warm=("b:2b",)), "a:1b")
        marks = {o.value: o.trailing for o in options}
        assert marks["b:2b"] == "im Speicher" and marks["a:1b"] == ""

    def test_several_warm_models(self):
        options = SW.ollama_options(state_with(warm=("a:1b", "b:2b")), "a:1b")
        assert all(o.trailing == "im Speicher" for o in options if o.enabled)

    def test_pull_hint_is_present_but_not_selectable(self):
        options = SW.ollama_options(state_with(), "a:1b")
        hint = options[-1]
        assert hint.label == ollama.pull_hint()
        assert hint.enabled is False and hint.value == ""

    def test_no_hint_without_a_service(self):
        options = SW.ollama_options(state_with(online=False, names=()), "a:1b")
        assert all(o.label != ollama.pull_hint() for o in options)

    def test_no_invented_catalog(self):
        """Ollama kennt ueber diese API keine entfernten Modelle."""
        options = SW.ollama_options(state_with(names=("a:1b",)), "a:1b")
        assert [o.value for o in options if o.enabled] == ["a:1b"]

    def test_a_missing_configured_model_stays_visible(self):
        options = SW.ollama_options(state_with(), "weg:9b")
        first = options[0]
        assert first.value == "weg:9b" and first.enabled is False
        assert first.meta == "Nicht installiert"

    def test_a_missing_model_is_not_replaced(self):
        options = SW.ollama_options(state_with(), "weg:9b")
        assert [o.value for o in options if o.enabled] == ["a:1b", "b:2b"]

    def test_an_unchecked_service_claims_nothing(self):
        """Vor der ersten Antwort steht keine Aussage ueber den Dienst da."""
        options = SW.ollama_options(ollama.OllamaState(False), "a:1b")
        assert options[0].value == "a:1b" and options[0].meta == ""

    def test_offline_keeps_the_configured_name_readable(self):
        options = SW.ollama_options(state_with(online=False, names=()), "a:1b")
        assert options[0].value == "a:1b"
        assert "nicht erreichbar" in options[0].meta

    def test_empty_service_and_empty_config(self):
        assert SW.ollama_options(state_with(online=False, names=()), "") == []


# ------------------------------------------------------------- Persistenz


class _Row:
    def __init__(self):
        self.enabled = True
        self.description = ""
        self.status = None
        self.text = ""

    def set_enabled(self, value):
        self.enabled = value

    def set_description(self, text):
        self.description = text

    def set_status(self, status, text):
        self.status, self.text = status, text


class _Select:
    def __init__(self, value=""):
        self.value = value
        self.options = []

    def set_value(self, value, notify=False):
        self.value = value

    def set_options(self, options):
        self.options = options


@pytest.fixture
def window(monkeypatch):
    win = SW.__new__(SW)
    win.pull_row = win.pull_task = None  # the Ollama section of a window that never built it
    win._alive = True
    win.win = type("W", (), {"winfo_exists": lambda self: True})()
    win.app = type("A", (), {"ollama_state": state_with(), "ollama_checked": True})()
    win._ollama_online = True
    win._ollama_state = state_with()
    win._ollama_signature = win._ollama_state.signature()
    win.row_cleanup = _Row()
    win.row_cleanup_model = _Row()
    win.row_prompt_model = _Row()
    win.select_cleanup_model = _Select("a:1b")
    win.select_prompt_model = _Select("b:2b")
    monkeypatch.setattr("hams.config.CFG.ollama_model_cleanup", "a:1b")
    monkeypatch.setattr("hams.config.CFG.ollama_model_prompt", "b:2b")
    win.written = []
    monkeypatch.setattr(
        "hams.settings_status._persist_config_value",
        lambda section, key, value: win.written.append((section, key, value)),
    )
    return win


class TestSelection:
    def test_cleanup_writes_only_its_own_key(self, window):
        from hams.config import CFG

        window._select_cleanup_model("b:2b")
        assert window.written == [("ollama", "model_transcript_cleanup", "b:2b")]
        assert CFG.ollama_model_cleanup == "b:2b"

    def test_cleanup_does_not_touch_the_prompt_model(self, window):
        from hams.config import CFG

        before = CFG.ollama_model_prompt
        window._select_cleanup_model("b:2b")
        assert CFG.ollama_model_prompt == before
        assert all(key != "model_prompt_generate" for _s, key, _v in window.written)

    def test_prompt_writes_only_its_own_key(self, window):
        from hams.config import CFG

        window._select_prompt_model("a:1b")
        assert window.written == [("ollama", "model_prompt_generate", "a:1b")]
        assert CFG.ollama_model_prompt == "a:1b"

    def test_prompt_does_not_touch_the_cleanup_model(self, window):
        from hams.config import CFG

        before = CFG.ollama_model_cleanup
        window._select_prompt_model("a:1b")
        assert CFG.ollama_model_cleanup == before
        assert all(key != "model_transcript_cleanup" for _s, key, _v in window.written)

    def test_the_two_stay_different(self, window):
        from hams.config import CFG

        window._select_cleanup_model("a:1b")  # bereits gesetzt -> nichts
        window._select_prompt_model("b:2b")  # bereits gesetzt -> nichts
        assert window.written == []
        window._select_cleanup_model("b:2b")
        window._select_prompt_model("a:1b")
        assert CFG.ollama_model_cleanup == "b:2b"
        assert CFG.ollama_model_prompt == "a:1b"

    def test_choosing_the_same_value_writes_nothing(self, window):
        window._select_cleanup_model("a:1b")
        window._select_prompt_model("b:2b")
        assert window.written == []

    def test_the_hint_cannot_be_chosen(self, window):
        window._select_cleanup_model("")
        window._select_prompt_model("")
        assert window.written == []


class TestApplyState:
    def test_online_state_reaches_the_row(self, window):
        SW._apply_ollama_state(window, state_with(names=("a:1b", "c:3b")))
        assert window._ollama_online
        assert window.row_cleanup_model.status == "ready"

    def test_offline_state_is_named(self, window):
        SW._apply_ollama_state(window, state_with(online=False, names=()))
        assert not window._ollama_online
        assert window.row_cleanup_model.status == "error"
        assert "nicht erreichbar" in window.row_cleanup_model.text

    def test_the_state_is_kept_by_the_application(self, window):
        """Ein neu geoeffnetes Fenster soll nicht bei null anfangen."""
        state = state_with(names=("a:1b", "neu:1b"))
        SW._apply_ollama_state(window, state)
        assert window.app.ollama_state is state
        assert window.app.ollama_checked is True

    def test_nothing_is_claimed_before_the_first_answer(self, window):
        """ "Nicht erreichbar" waere eine Behauptung ohne Messung."""
        window.app.ollama_checked = False
        SW._show_ollama_status(window)
        assert window.row_cleanup_model.status == "idle"
        assert "geprüft" in window.row_cleanup_model.text

    def test_a_known_state_is_shown_again(self, window):
        window.app.ollama_checked = True
        window._ollama_state = state_with(online=False, names=())
        SW._show_ollama_status(window)
        assert window.row_cleanup_model.status == "error"

    def test_unchanged_answer_does_not_rebuild(self, window):
        """Sonst faellt alle fuenf Sekunden das Popover zu."""
        rebuilt = []
        window._refresh_ollama_models = lambda: rebuilt.append(1)
        SW._apply_ollama_state(window, state_with())
        assert rebuilt == []

    def test_a_changed_answer_rebuilds(self, window):
        rebuilt = []
        window._refresh_ollama_models = lambda: rebuilt.append(1)
        SW._apply_ollama_state(window, state_with(names=("a:1b", "b:2b", "neu:1b")))
        assert rebuilt == [1]

    def test_a_warm_model_alone_rebuilds(self, window):
        rebuilt = []
        window._refresh_ollama_models = lambda: rebuilt.append(1)
        SW._apply_ollama_state(window, state_with(warm=("a:1b",)))
        assert rebuilt == [1]

    def test_a_dead_window_is_left_alone(self, window):
        window._alive = False
        SW._apply_ollama_state(window, state_with(names=("x:1b",)))
        assert window.select_cleanup_model.options == []

    def test_refresh_fills_both_selects(self, window):
        window._ollama_state = state_with(names=("a:1b", "b:2b"), warm=("a:1b",))
        SW._refresh_ollama_models(window)
        chosen = [o.value for o in window.select_cleanup_model.options if o.enabled]
        assert chosen == ["a:1b", "b:2b"]
        assert window.select_cleanup_model.value == "a:1b"
        assert window.select_prompt_model.value == "b:2b"


class TestDisplayName:
    """Die Auswahl zeigt lesbare Namen, gespeichert bleibt der rohe."""

    @pytest.mark.parametrize(
        "raw,shown",
        [
            ("qwen2.5:7b", "Qwen 2.5 · 7B"),
            ("ministral-3:8b", "Ministral 3 · 8B"),
            ("llama3.2:3b", "Llama 3.2 · 3B"),
            ("deepseek-r1:14b", "DeepSeek R1 · 14B"),
            ("gpt-oss:20b", "GPT OSS · 20B"),
            ("mistral:latest", "Mistral"),
            ("gemma3:4b-it-qat", "Gemma 3 · 4B-it-qat"),
            ("hf.co/nutzer/phi3:mini", "Phi 3 · mini"),
        ],
    )
    def test_readable(self, raw, shown):
        assert ollama.display_name(raw) == shown

    def test_options_show_the_readable_name_but_keep_the_raw_value(self):
        state = ollama.OllamaState(True, models=[ollama.OllamaModel("qwen2.5:7b")])
        option = SW.ollama_options(state, "qwen2.5:7b")[0]
        assert option.value == "qwen2.5:7b"
        assert option.label == "Qwen 2.5 · 7B"

    def test_a_missing_model_is_shown_readably_too(self):
        state = ollama.OllamaState(True, models=[])
        option = SW.ollama_options(state, "llama3.2:3b")[0]
        assert (option.value, option.label) == ("llama3.2:3b", "Llama 3.2 · 3B")


# ------------------------------------------------------------------- generate and warm-up


class _Lines(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def recording_opener(answer: bytes):
    """An opener that records (url, body, timeout) and answers `answer`."""
    sent = []

    def opener(request, timeout=None):
        sent.append((request.full_url, json.loads(request.data), timeout))
        return _Lines(answer)

    return opener, sent


class TestGenerate:
    def test_a_plain_request_with_thinking_switched_off(self):
        """Qwen 3.5 thought for 7 s about an "ok" without `think: false`."""
        opener, sent = recording_opener(b'{"response": " ok "}')
        client = ollama.OllamaClient(BASE, opener=opener)
        assert client.generate("Hallo", "qwen3.5:9b", timeout=5, keep_alive="30m", options={"seed": 0}) == "ok"
        url, body, timeout = sent[0]
        assert url == f"{BASE}/api/generate" and timeout == 5
        assert body == {
            "model": "qwen3.5:9b",
            "prompt": "Hallo",
            "stream": False,
            "keep_alive": "30m",
            "think": False,
            "options": {"seed": 0},
        }

    def test_the_system_field_is_sent_only_when_given(self):
        opener, sent = recording_opener(b'{"response": "ok"}')
        client = ollama.OllamaClient(BASE, opener=opener)
        client.generate("p", "m", timeout=1, keep_alive="1m")
        client.generate("p", "m", timeout=1, keep_alive="1m", system="Klartext")
        assert "system" not in sent[0][1] and sent[1][1]["system"] == "Klartext"

    @pytest.mark.parametrize("answer", [b'{"error": "model not found"}', b'{"done": true}', b"[1]"])
    def test_an_answer_without_text_is_an_error(self, answer):
        opener, _sent = recording_opener(answer)
        with pytest.raises(ValueError):
            ollama.OllamaClient(BASE, opener=opener).generate("p", "m", timeout=1, keep_alive="1m")

    def test_a_stream_reports_its_length_and_stops_at_done(self):
        lines = b'{"response": "Hallo "}\n\nkein json\n{"response": "Welt"}\n{"done": true}\n{"response": "zu spaet"}\n'
        opener, sent = recording_opener(lines)
        reported = []
        answer = ollama.OllamaClient(BASE, opener=opener).generate(
            "p", "m", timeout=1, keep_alive="1m", on_progress=reported.append
        )
        assert answer == "Hallo Welt"
        assert sent[0][1]["stream"] is True
        assert reported and reported[0] == 6

    def test_a_cut_off_stream_is_an_error(self):
        opener, _sent = recording_opener(b'{"response": "Os"}\n')
        with pytest.raises(ollama.OllamaAnswerError):
            ollama.OllamaClient(BASE, opener=opener).generate(
                "p", "m", timeout=1, keep_alive="1m", on_progress=lambda n: None
            )

    def test_the_address_follows_the_setting(self):
        opener, sent = recording_opener(b'{"response": "ok"}')
        url = ["http://127.0.0.1:11434/api/generate"]
        client = ollama.OllamaClient.for_url(lambda: url[0], opener=opener)
        client.generate("p", "m", timeout=1, keep_alive="1m")
        url[0] = "http://10.0.0.2:9999/api/generate"
        client.generate("p", "m", timeout=1, keep_alive="1m")
        assert [entry[0] for entry in sent] == [
            "http://127.0.0.1:11434/api/generate",
            "http://10.0.0.2:9999/api/generate",
        ]

    def test_warm_up_sends_an_empty_prompt(self):
        opener, sent = recording_opener(b"{}")
        ollama.OllamaClient(BASE, opener=opener).warm_up("qwen3.5:9b", "30m")
        assert sent == [
            (
                f"{BASE}/api/generate",
                {"model": "qwen3.5:9b", "prompt": "", "stream": False, "keep_alive": "30m"},
                60.0,
            )
        ]

    @pytest.mark.parametrize(
        ("error", "unreachable"),
        [
            (ConnectionRefusedError("refused"), True),
            (TimeoutError("timed out"), True),
            (urllib.error.HTTPError(BASE, 500, "boom", {}, None), False),
            (ValueError("bad json"), False),
        ],
    )
    def test_only_a_missing_service_counts_as_unreachable(self, error, unreachable):
        assert ollama.is_unreachable(error) is unreachable


class TestCleanupEvalOptions:
    """`tools/cleanup_eval.py --options` merges what-if options into its own client, nothing global."""

    @pytest.fixture
    def tool(self, monkeypatch):
        path = Path(__file__).resolve().parent.parent / "tools" / "cleanup_eval.py"
        monkeypatch.syspath_prepend(str(path.parent))  # the tool imports its neighbour wer_benchmark
        spec = importlib.util.spec_from_file_location("cleanup_eval_under_test", path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)  # its dataclasses look themselves up there
        spec.loader.exec_module(module)
        return module

    def test_the_options_are_merged_over_hams_own(self, tool):
        urlopen = urllib.request.urlopen
        opener, sent = recording_opener(b'{"response": "ok"}')
        client = ollama.OllamaClient(BASE, opener=opener)
        tool.add_ollama_options(client, {"temperature": 1})
        client.generate("p", "m", timeout=1, keep_alive="1m", options={"temperature": 0, "seed": 0})
        assert sent[0][1]["options"] == {"temperature": 1, "seed": 0}
        assert urllib.request.urlopen is urlopen, "the tool must not patch urlopen for the whole process"
