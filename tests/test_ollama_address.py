"""Die Adresse, unter der Ollama angesprochen wird.

Gemessen auf Windows 11: `localhost` loest zu `::1` und `127.0.0.1` auf, `::1`
steht vorn, und Ollama lauscht in der Voreinstellung nur auf IPv4. Der tote
erste Versuch kostet 2,03 s — vor jedem Cleanup, jeder Prompt-Antwort, jedem
Warmlauf und jeder Auskunft des Einstellungsfensters.

Diese Datei haelt die Regel fest, die daraus folgt: der Hostname `localhost`
wird auf `127.0.0.1` gelegt, und sonst wird an keiner Adresse etwas geaendert.
"""

import pytest

from wisper.config import Config, _apply_toml, ipv4_loopback
from wisper.config_validation import ollama_url_problem
from wisper import ollama as ollama_api


class TestLocalhostWirdUmgelegt:
    """Nur der eine Hostname, dafuer zuverlaessig."""

    def test_the_plain_case(self):
        assert (ipv4_loopback("http://localhost:11434/api/generate")
                == "http://127.0.0.1:11434/api/generate")

    def test_case_does_not_matter(self):
        assert (ipv4_loopback("http://LocalHost:11434/api/generate")
                == "http://127.0.0.1:11434/api/generate")

    def test_without_a_port(self):
        assert ipv4_loopback("http://localhost/api/generate") == "http://127.0.0.1/api/generate"

    def test_path_and_query_survive(self):
        assert (ipv4_loopback("http://localhost:11434/api/generate?x=1#z")
                == "http://127.0.0.1:11434/api/generate?x=1#z")

    def test_credentials_survive(self):
        assert (ipv4_loopback("http://user:pw@localhost:11434/api/generate")
                == "http://user:pw@127.0.0.1:11434/api/generate")


class TestAllesAndereBleibt:
    """Eine Umschreibung, die mehr anfasst als noetig, waere ein neuer Fehler."""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:11434/api/generate",
        # Wer IPv6 ausdruecklich hinschreibt, meint es auch — das ist der
        # Ausweg fuer eine Instanz, die nur auf ::1 lauscht.
        "http://[::1]:11434/api/generate",
        "http://192.168.1.5:11434/api/generate",
        "http://ollama.lan:11434/api/generate",
        # Kein Teiltreffer: der Host heisst nicht "localhost".
        "http://localhost.example.com:11434/api/generate",
        "http://mylocalhost:11434/api/generate",
        "https://localhost.localdomain/api/generate",
    ])
    def test_stays_untouched(self, url):
        assert ipv4_loopback(url) == url

    @pytest.mark.parametrize("url", ["", "kein-url", "http://", "http://localhost:nichts/x"])
    def test_unusable_input_is_passed_through(self, url):
        assert ipv4_loopback(url) == url


class TestDieKonfigurationBenutztEs:
    """Der Weg, den die Anwendung tatsaechlich geht."""

    def test_a_user_file_with_localhost_is_corrected(self):
        cfg = Config()
        _apply_toml(cfg, {"ollama": {"url": "http://localhost:11434/api/generate"}})
        assert cfg.ollama_url == "http://127.0.0.1:11434/api/generate"

    def test_the_default_needs_no_correction(self):
        assert ipv4_loopback(Config().ollama_url) == Config().ollama_url
        assert "localhost" not in Config().ollama_url

    def test_the_service_address_follows(self):
        """Tags und /api/ps haengen an derselben Adresse — sonst zahlt die
        periodische Auskunft des Einstellungsfensters die 2 s weiter."""
        cfg = Config()
        _apply_toml(cfg, {"ollama": {"url": "http://localhost:11434/api/generate"}})
        assert ollama_api.base_url(cfg.ollama_url) == "http://127.0.0.1:11434"


class TestDiktateBleibenImHaus:
    """Jedes Diktat geht an diese Adresse. Unverschluesselt nur an den eigenen Rechner."""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:11434/api/generate",
        "http://127.8.0.1:11434/api/generate",
        "http://[::1]:11434/api/generate",
        "http://localhost:11434/api/generate",
        "https://ollama.lan/api/generate",
        "https://192.168.1.5:11434/api/generate",
    ])
    def test_local_or_encrypted_is_allowed(self, url):
        assert ollama_url_problem(url) == ""

    @pytest.mark.parametrize("url, reason", [
        ("http://192.168.1.5:11434/api/generate", "https"),
        ("http://ollama.lan:11434/api/generate", "https"),
        ("http://localhost.example.com/api/generate", "https"),
        ("file:///C:/Windows/win.ini", "http://"),
        ("ftp://127.0.0.1/api/generate", "http://"),
        ("http:///api/generate", "http://"),
        ("kein-url", "http://"),
        ("http://[zz/api", "valid"),
    ])
    def test_everything_else_is_refused_with_a_reason(self, url, reason):
        assert reason in ollama_url_problem(url)

    def test_a_refused_address_is_not_used(self, caplog):
        cfg = Config()
        with caplog.at_level("WARNING", logger="wisper"):
            _apply_toml(cfg, {"ollama": {"url": "http://192.168.1.5:11434/api/generate"}})
        assert cfg.ollama_url == Config().ollama_url
        assert "must use https" in caplog.text

    def test_an_encrypted_remote_address_is_used(self):
        cfg = Config()
        _apply_toml(cfg, {"ollama": {"url": "https://ollama.lan/api/generate"}})
        assert cfg.ollama_url == "https://ollama.lan/api/generate"
