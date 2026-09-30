"""Tests der Ollama-Auskunft und der beiden Modellauswahlen.

Der Dienst wird hier nicht angesprochen: `urlopen` wird ersetzt, damit auch
Zeitüberschreitung, kaputte Antwort und fehlendes `/api/ps` prüfbar sind. Der
Lauf gegen das echte Ollama lief als Integrationstest.
"""

import json
import urllib.error
import urllib.request

import pytest

from wisper import ollama
from wisper.main import SettingsWindow as SW

TAGS = {
    "models": [
        {"name": "ministral-3:8b", "model": "ministral-3:8b", "size": 6_022_236_616,
         "details": {"parameter_size": "8.9B", "family": "mistral3",
                     "quantization_level": "Q4_K_M"}},
        {"name": "qwen3.5:9b", "model": "qwen3.5:9b", "size": 6_594_474_711,
         "details": {"parameter_size": "9.7B", "family": "qwen35"}},
        {"name": "qwen2.5:3b", "model": "qwen2.5:3b", "size": 1_929_912_432,
         "details": {"parameter_size": "3.1B", "family": "qwen2"}},
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
    @pytest.mark.parametrize("configured,expected", [
        ("http://localhost:11434/api/generate", "http://localhost:11434"),
        ("http://127.0.0.1:11434/api/chat", "http://127.0.0.1:11434"),
        ("http://box:11434", "http://box:11434"),
        ("http://box:11434/", "http://box:11434"),
    ])
    def test_service_address_is_derived(self, configured, expected):
        assert ollama.base_url(configured) == expected

    def test_empty_configuration_does_not_raise(self):
        assert ollama.base_url("") == ""


# -------------------------------------------------------------------- /api/tags


class TestListModels:
    def test_several_models(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})
        models, error = ollama.list_models(BASE)
        assert error == ""
        assert [m.name for m in models] == ["ministral-3:8b", "qwen3.5:9b", "qwen2.5:3b"]

    def test_empty_list_is_not_an_error(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": {"models": []}})
        models, error = ollama.list_models(BASE)
        assert models == () and error == ""

    def test_timeout_is_reported_in_plain_words(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TimeoutError("timed out")})
        models, error = ollama.list_models(BASE)
        assert models == () and error == "Ollama nicht erreichbar"

    def test_service_down(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": ConnectionRefusedError("refused")})
        _models, error = ollama.list_models(BASE)
        assert error == "Ollama nicht erreichbar"

    def test_http_error_names_the_code(self, monkeypatch):
        fake_urlopen(monkeypatch, {
            "/api/tags": urllib.error.HTTPError(BASE, 500, "boom", {}, None)})
        _models, error = ollama.list_models(BASE)
        assert "500" in error

    def test_broken_answer(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": b"<html>kein json</html>"})
        models, error = ollama.list_models(BASE)
        assert models == () and "unverständlich" in error

    def test_entries_without_a_name_are_skipped(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": {"models": [{"size": 1}, "quatsch"]}})
        models, error = ollama.list_models(BASE)
        assert models == () and error == ""

    def test_missing_details_are_left_out(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": {"models": [{"name": "nackt:1b"}]}})
        models, _error = ollama.list_models(BASE)
        assert models[0].meta == "", "nichts erfinden, was die API nicht liefert"


class TestMeta:
    def test_parameters_and_size(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})
        models, _error = ollama.list_models(BASE)
        assert models[0].meta == "8,9B · 6,0 GB"

    def test_only_a_size(self):
        assert ollama.OllamaModel("x", 1_929_912_432).meta == "1,9 GB"

    def test_only_parameters(self):
        assert ollama.OllamaModel("x", 0, "7.6B").meta == "7,6B"


# --------------------------------------------------------------------- /api/ps


class TestRunningModels:
    def test_one_warm_model(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/ps": PS_ONE})
        assert ollama.running_models(BASE) == frozenset({"ministral-3:8b"})

    def test_several_warm_models(self, monkeypatch):
        """keep_alive haelt durchaus mehrere Modelle gleichzeitig im Speicher."""
        fake_urlopen(monkeypatch, {"/api/ps": PS_TWO})
        assert ollama.running_models(BASE) == frozenset({"ministral-3:8b", "qwen2.5:3b"})

    def test_missing_endpoint_is_tolerated(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})      # /api/ps -> 404
        assert ollama.running_models(BASE) == frozenset()

    def test_nothing_warm(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/ps": {"models": []}})
        assert ollama.running_models(BASE) == frozenset()


class TestProbe:
    def test_online_with_warm_model(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS, "/api/ps": PS_ONE})
        state = ollama.probe(BASE)
        assert state.online and len(state.models) == 3
        assert state.warm == frozenset({"ministral-3:8b"})

    def test_online_without_ps(self, monkeypatch):
        """Ohne /api/ps muss die Auswahl trotzdem funktionieren."""
        fake_urlopen(monkeypatch, {"/api/tags": TAGS})
        state = ollama.probe(BASE)
        assert state.online and state.warm == frozenset()
        assert len(state.models) == 3

    def test_offline_does_not_ask_for_ps(self, monkeypatch):
        calls = fake_urlopen(monkeypatch, {"/api/tags": TimeoutError("x")})
        state = ollama.probe(BASE)
        assert not state.online and state.error
        assert not any("/api/ps" in url for url in calls)

    def test_find_and_names(self, monkeypatch):
        fake_urlopen(monkeypatch, {"/api/tags": TAGS, "/api/ps": PS_ONE})
        state = ollama.probe(BASE)
        assert state.find("qwen3.5:9b").parameter_size == "9.7B"
        assert state.find("gibtsnicht") is None
        assert "qwen2.5:3b" in state.names


class TestSignature:
    def _state(self, monkeypatch, tags, ps):
        fake_urlopen(monkeypatch, {"/api/tags": tags, "/api/ps": ps})
        return ollama.probe(BASE)

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
        assert ollama.probe(BASE).signature() != first.signature()


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
        assert hint.label == ollama.PULL_HINT
        assert hint.enabled is False and hint.value == ""

    def test_no_hint_without_a_service(self):
        options = SW.ollama_options(state_with(online=False, names=()), "a:1b")
        assert all(o.label != ollama.PULL_HINT for o in options)

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
    monkeypatch.setattr("wisper.main.CFG.ollama_model_cleanup", "a:1b")
    monkeypatch.setattr("wisper.main.CFG.ollama_model_prompt", "b:2b")
    win.written = []
    monkeypatch.setattr("wisper.main._persist_config_value",
                        lambda section, key, value: win.written.append((section, key, value)))
    return win


class TestSelection:
    def test_cleanup_writes_only_its_own_key(self, window):
        from wisper.main import CFG

        window._select_cleanup_model("b:2b")
        assert window.written == [("ollama", "model_transcript_cleanup", "b:2b")]
        assert CFG.ollama_model_cleanup == "b:2b"

    def test_cleanup_does_not_touch_the_prompt_model(self, window):
        from wisper.main import CFG

        before = CFG.ollama_model_prompt
        window._select_cleanup_model("b:2b")
        assert CFG.ollama_model_prompt == before
        assert all(key != "model_prompt_generate" for _s, key, _v in window.written)

    def test_prompt_writes_only_its_own_key(self, window):
        from wisper.main import CFG

        window._select_prompt_model("a:1b")
        assert window.written == [("ollama", "model_prompt_generate", "a:1b")]
        assert CFG.ollama_model_prompt == "a:1b"

    def test_prompt_does_not_touch_the_cleanup_model(self, window):
        from wisper.main import CFG

        before = CFG.ollama_model_cleanup
        window._select_prompt_model("a:1b")
        assert CFG.ollama_model_cleanup == before
        assert all(key != "model_transcript_cleanup" for _s, key, _v in window.written)

    def test_the_two_stay_different(self, window):
        from wisper.main import CFG

        window._select_cleanup_model("a:1b")      # bereits gesetzt -> nichts
        window._select_prompt_model("b:2b")       # bereits gesetzt -> nichts
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
        """"Nicht erreichbar" waere eine Behauptung ohne Messung."""
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
