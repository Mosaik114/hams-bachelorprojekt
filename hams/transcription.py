"""Aus der Tonspur wird Text: Whisper, Bereinigung mit Ollama, Einfuegen."""

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import numpy as np
import pyperclip

if sys.platform == "win32":
    import keyboard
else:
    from hams.linux import keyboard

from hams import glossary
from hams.backend import BatchedInferencePipeline
from hams.config import CFG, OUTPUT_DIR, logger
from hams.recording import MODEL_WAIT_SECONDS, _normalize_audio

#: Ab dieser Aufnahmelaenge wird die gebatchte Auswertung benutzt.
#: Gemessen (turbo/float16, RTX 5070, gegen bekannten Referenztext):
#:    6 s: −1,6 % Zeit,  WER ±0,00 pp      24 s: +0,5 % Zeit,  WER ±0,00 pp
#:   12 s: ±0,0 % Zeit,  WER ±0,00 pp      48 s: +25,6 % Zeit, WER −2,38 pp
#:                                         84 s: +34,5 % Zeit, WER −1,32 pp
#: Unterhalb der Schwelle bringt es nichts, oberhalb wird es schneller *und*
#: genauer: der sequentielle Lauf verschluckt am Ende langer Aufnahmen Woerter
#: und haengt Floskeln an, die nie gesprochen wurden.
BATCHED_MIN_SECONDS = 30.0
#: Groesser bringt nichts mehr (8 -> 16 gemessen: +1,4 %), kostet aber VRAM.
BATCH_SIZE = 8


#: Ein Codeblock, der die **ganze** Antwort umschliesst. Nur dieser wird
#: entfernt — Backticks mitten im Text gehoeren dem Nutzer.
_FENCE_RE = re.compile(r"\A```[^\n]*\n(?P<body>.*)\n```\Z", re.DOTALL)

#: Markdown-Hervorhebungen. Jede Regel verlangt, dass der Inhalt weder mit
#: Leerraum beginnt noch endet und dass links und rechts kein Wortzeichen
#: steht. Damit bleiben `2 * 3 = 6`, `*.txt`, `foo*bar` und `datei_name.txt`
#: unangetastet — dort fehlt jeweils genau eine dieser Bedingungen.
_EMPHASIS_RES = (
    re.compile(r"(?<![\w*])\*\*\*(?!\s)(?P<text>[^*\n]+?)(?<!\s)\*\*\*(?![\w*])"),
    re.compile(r"(?<![\w_])___(?!\s)(?P<text>[^_\n]+?)(?<!\s)___(?![\w_])"),
    re.compile(r"(?<![\w*])\*\*(?!\s)(?P<text>[^*\n]+?)(?<!\s)\*\*(?![\w*])"),
    re.compile(r"(?<![\w_])__(?!\s)(?P<text>[^_\n]+?)(?<!\s)__(?![\w_])"),
    re.compile(r"(?<![\w*])\*(?!\s)(?P<text>[^*\n]+?)(?<!\s)\*(?![\w*])"),
    re.compile(r"(?<![\w_])_(?!\s)(?P<text>[^_\n]+?)(?<!\s)_(?![\w_])"),
)


#: Wird dem Prompt-Modus als Systemanweisung mitgegeben. Nicht in den Prompt
#: des Nutzers hineingeschrieben: der bleibt Wort fuer Wort, wie diktiert.
#: Ollama nimmt dafuer ein eigenes Feld entgegen.
PROMPT_PLAIN_SYSTEM = (
    "Antworte in normalem Klartext. Verwende kein Markdown: keine Sternchen "
    "oder Unterstriche zur Hervorhebung, keine Fett- oder Kursivschrift, keine "
    "Überschriften, keine Backticks und keine Codeblöcke. Deine Antwort wird "
    "unverändert in ein Textfeld eingefügt."
)


def plain_text(value: str) -> str:
    """Nimmt Markdown-Hervorhebungen aus einer Cleanup-Antwort.

    Bewusst klein gehalten: keine Markdown-Bibliothek, keine allgemeine
    Umwandlung nach Klartext. Entfernt werden genau zwei Dinge — ein
    Codeblock, der die ganze Antwort umschliesst, und die vier ueblichen
    Hervorhebungsklammern `**`, `__`, `*`, `_`.

    Ein pauschales `text.replace("*", "")` waere hier falsch: es zerlegte
    `2 * 3 = 6`, `*.txt` und `foo*bar`. Deshalb muessen die Klammern wirklich
    als Klammern erkennbar sein.

    Gilt nur fuer den Cleanup. Der Prompt-Modus liefert weiterhin, was das
    Modell schreibt — dort ist Formatierung erwuenscht.
    """
    if not value:
        return value
    text = value.strip()
    fence = _FENCE_RE.match(text)
    if fence is not None:
        text = fence.group("body").strip()
    for pattern in _EMPHASIS_RES:
        previous = None
        while previous != text:  # geschachtelte Klammern aufloesen
            previous = text
            text = pattern.sub(r"\g<text>", text)
    return text


class TranscriptionMixin:
    """Transkription, Bereinigung mit Ollama und Einfuegen des Textes."""

    # ------------------------------------------------------------------ transcription

    def _ollama_generate(
        self,
        prompt: str,
        model: str,
        timeout: int,
        stream: bool = False,
        progress_label: str | None = None,
        system: str | None = None,
    ) -> str:
        """Sendet den Prompt an Ollama. stream=True ist nur für lange Antworten
        sinnvoll (Prompt-Modus); für kurze Cleanup-Antworten lohnt der Overhead
        nicht. keep_alive sorgt dafür, dass das Modell zwischen Aufrufen im
        VRAM bleibt und nicht 10–30s nachgeladen werden muss.

        `system` geht als eigenes Feld an Ollama. So lässt sich eine Regel für
        die Form der Antwort setzen, ohne den Prompt des Nutzers anzufassen."""
        body = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "keep_alive": CFG.ollama_keep_alive,
            # Denkende Modelle (Qwen 3.5, Granite 4.2) überlegen sonst erst
            # sekundenlang, bevor sie antworten. Für Diktat und Prompt zählt
            # die Zeit. Modelle ohne Denkmodus übergehen das Feld.
            "think": False,
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode()
        # Adresse aus der Einstellung, Schema/Host geprüft in config_validation.ollama_url_problem
        req = urllib.request.Request(  # noqa: S310
            CFG.ollama_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if not stream:
                result = json.loads(resp.read())
                return result["response"].strip()

            return self._read_ollama_stream(resp, show_progress=bool(progress_label))

    def _read_ollama_stream(self, resp, show_progress: bool) -> str:
        """Die gestreamte Antwort Zeile für Zeile; zeigt dabei höchstens viermal pro Sekunde die Zeichenzahl."""
        chunks: list[str] = []
        last_update = 0.0
        for raw_line in resp:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ollama stream: non-JSON line ignored")
                continue
            chunks.append(obj.get("response", ""))
            if obj.get("done"):
                break
            now = time.monotonic()
            if show_progress and now - last_update > 0.25:
                last_update = now
                total_len = sum(len(c) for c in chunks)
                self.root.after(0, lambda n=total_len: self.elapsed_label.config(text=f"{n} Zeichen"))
        return "".join(chunks).strip()

    def _generate_from_prompt(self, text: str) -> str:
        """Die KI-Antwort zum diktierten Prompt — ebenfalls als Klartext.

        Auch diese Antwort wird unverändert eingefügt; ein `**Oslo**` im
        Textfeld ist dort genauso unerwünscht wie im korrigierten Transkript.
        Zwei Ebenen wie beim Cleanup: eine Systemanweisung an Ollama und
        dieselbe Normalisierung danach. Der diktierte Prompt selbst bleibt
        unangetastet.

        Absätze und Zeilenumbrüche bleiben erhalten — im Prompt-Modus tragen
        sie Bedeutung, anders als eine Hervorhebung.
        """
        answer = self._ollama_generate(
            prompt=text,
            model=CFG.ollama_model_prompt,
            timeout=CFG.ollama_timeout_prompt,
            stream=CFG.ollama_stream_prompt,
            progress_label=f"Generiere ({CFG.ollama_model_prompt})",
            system=PROMPT_PLAIN_SYSTEM,
        )
        return plain_text(answer)

    def _cleanup_text(self, text: str) -> str:
        """Clean up transcript text using LLM.

        Removes stuttering, repetitions, and slips of the tongue.
        Corrects spelling and grammar while preserving original phrasing.

        Die Antwort geht durch `plain_text()`, bevor sie irgendwohin fliesst.
        Der Prompt verbietet Markdown, aber ein Modell haelt sich nicht immer
        daran — und was hier durchrutscht, landet als sichtbares `**` im
        Eingabefeld des Nutzers.
        """
        template = CFG.cleanup_prompt_template
        prompt = template.replace("{text}", text) if "{text}" in template else f"{template}\n\nText: {text}"
        if hinweis := glossary.cleanup_hint():
            prompt = f"{hinweis}\n\n{prompt}"
        answer = self._ollama_generate(
            prompt=prompt,
            model=CFG.ollama_model_cleanup,
            timeout=CFG.ollama_timeout_cleanup,
            stream=False,
        )
        return plain_text(answer)

    def _save_transcript_file(self, text: str) -> None:
        """Save transcript to a timestamped file in the transcripts directory."""
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_file = OUTPUT_DIR / f"{stamp}.txt"
            out_file.write_text(text, encoding="utf-8")
            logger.info("Saved transcript: %s (%d chars)", out_file.name, len(text))
        except OSError:
            logger.exception("Failed to save transcript file")

    def _warmup_ollama_async(self) -> None:
        if not CFG.ollama_warmup_on_start:
            return
        threading.Thread(target=self._warmup_ollama, daemon=True, name="ollama-warmup").start()

    def _warmup_ollama(self) -> None:
        """Lädt das Cleanup-Modell proaktiv in den VRAM, damit der erste
        echte Request nicht mit einem Kaltstart von 10–30s büßt."""
        if not CFG.ollama_cleanup_enabled:
            return
        model = CFG.ollama_model_cleanup
        try:
            payload = json.dumps(
                {
                    "model": model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": CFG.ollama_keep_alive,
                }
            ).encode()
            # Adresse aus der Einstellung, Schema/Host geprüft in config_validation.ollama_url_problem
            req = urllib.request.Request(  # noqa: S310
                CFG.ollama_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                resp.read()
            logger.info("Ollama warmed up: %s", model)
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("Ollama warmup failed for %s: %s", model, exc)

    def _copy_and_paste(self, text: str) -> None:
        """Copy text to clipboard and paste at cursor position.

        Waits up to clipboard_paste_timeout seconds for clipboard confirmation
        before sending Ctrl+V.
        """
        """Kopiert Text in die Zwischenablage und sendet Ctrl+V. Wartet nur
        kurz, bis der Inhalt bestätigt ist. Normalisiert \\r\\n → \\n, weil
        Windows beim Lesen Zeilenumbrüche umwandelt und sonst der Vergleich
        nie matcht."""
        pyperclip.copy(text)
        target = text.replace("\r\n", "\n")
        deadline = time.monotonic() + CFG.clipboard_paste_timeout
        while time.monotonic() < deadline:
            try:
                if pyperclip.paste().replace("\r\n", "\n") == target:
                    break
            except pyperclip.PyperclipException:
                logger.exception("Clipboard read failed")
                break
            time.sleep(0.01)
        keyboard.send("ctrl+v")

    def _run_transcription(self, model, audio: np.ndarray) -> str | None:
        """Fuehrt Whisper mit einer *festen* Modellreferenz aus.

        Bewusst getrennt, damit der Aufrufer den Transkriptionslock nur um
        diesen Teil legen muss — Cleanup und Einfuegen brauchen ihn nicht und
        wuerden einen Modellwechsel unnoetig lange blockieren.
        """
        optionen = {
            "language": CFG.transcription_language,
            "vad_filter": CFG.transcription_vad_filter,
            "word_timestamps": CFG.transcription_word_timestamps,
            "beam_size": CFG.transcription_beam_size,
            "condition_on_previous_text": CFG.transcription_condition_on_previous_text,
            "without_timestamps": CFG.transcription_without_timestamps,
            "hotwords": glossary.hotwords(),  # Glossar: Namen und Begriffe öfter richtig erkennen
        }
        try:
            dauer = audio.size / max(CFG.sample_rate, 1)
            if dauer >= BATCHED_MIN_SECONDS:
                # Erlaubt ist das nur, weil zwischen den Segmenten ohnehin kein
                # Kontext fliesst — `condition_on_previous_text` ist aus. Sonst
                # waere die parallele Auswertung nicht dasselbe Ergebnis.
                segments, _ = BatchedInferencePipeline(model=model).transcribe(
                    _normalize_audio(audio), batch_size=BATCH_SIZE, **optionen
                )
            else:
                segments, _ = model.transcribe(_normalize_audio(audio), **optionen)
            return glossary.apply_replacements(" ".join(segment.text.strip() for segment in segments).strip())
        except Exception:
            logger.exception("Whisper transcription failed")
            self.root.after(
                0, lambda: self._apply_state("error", "Transkription fehlgeschlagen", "Einzelheiten stehen im Log.")
            )
            self._prompt_mode = False
            return None

    def _transcribe_audio(self, audio: np.ndarray) -> None:
        """Transcription pipeline thread target.

        Runs Whisper transcription, optionally cleans up via LLM,
        saves to file, copies to clipboard, and pastes at cursor.
        """
        was_prompt_mode = self._prompt_mode
        if not self.model_ready.is_set() and not self._auf_modell_warten():
            return
        # Genau einmal eine stabile Referenz holen und den Lauf damit zu Ende
        # bringen; der Lock haelt einen Modellwechsel so lange zurueck.
        with self._transcribe_lock:
            model = self.current_model()
            if model is None:
                self.root.after(0, lambda: self._apply_state("error", "Kein Modell geladen", "Versuch es erneut."))
                self._prompt_mode = False
                return
            text = self._run_transcription(model, audio)
        if text is None:
            return
        try:
            if not text:
                logger.info("Transcription returned empty text")
                self.root.after(
                    0,
                    lambda: self._apply_state(
                        "no_signal",
                        "Keine Sprache erkannt",
                        "Die Aufnahme enthielt keine verständlichen Wörter.",
                    ),
                )
                return
            logger.info("Transcribed %d chars (prompt_mode=%s)", len(text), was_prompt_mode)
            text = self._nachbearbeiten(text, was_prompt_mode)
            if text is not None:
                self._abliefern(text)
        except Exception:
            logger.exception("Transcription pipeline failed")
            self.root.after(
                0,
                lambda: self._apply_state(
                    "error",
                    "Transkription fehlgeschlagen",
                    "Einzelheiten stehen im Log.",
                ),
            )
        finally:
            self._prompt_mode = False

    def _auf_modell_warten(self) -> bool:
        """Die Aufnahme durfte ohne Modell beginnen; hier wird es faellig. In aller Regel steht es laengst — der
        Nutzer hat waehrend des Ladens gesprochen. Nur der erste Griff kurz nach dem Anmelden wartet hier wirklich,
        und dann sagt die Statuszeile auch, worauf. False = kein Modell (Fehler ist schon gemeldet)."""
        logger.info("Aufnahme wartet auf das Modell")
        self.root.after(
            0,
            lambda: self._apply_state(
                "transcribing",
                "Modell wird noch geladen …",
                "Die Aufnahme ist gesichert und wird gleich ausgewertet.",
            ),
        )
        if not self._await_model(time.monotonic() + MODEL_WAIT_SECONDS):
            logger.error("Kein Modell fuer eine fertige Aufnahme verfuegbar")
            self.root.after(
                0,
                lambda: self._apply_state(
                    "error",
                    "Modell nicht verfügbar",
                    "Die Aufnahme liess sich nicht auswerten.",
                ),
            )
            self._prompt_mode = False
            return False
        self.root.after(0, lambda: self._apply_state("transcribing"))
        return True

    def _nachbearbeiten(self, text: str, was_prompt_mode: bool) -> str | None:
        """Prompt-Modus: KI-Antwort statt Diktat; sonst (falls an) Bereinigung. None = abgebrochen (gemeldet)."""
        if was_prompt_mode:
            self.root.after(0, lambda: self._apply_state("generating"))
            try:
                return self._generate_from_prompt(text)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
                logger.exception("Ollama prompt-generation failed")
                self.root.after(
                    0,
                    lambda: self._apply_state(
                        "error",
                        "KI-Antwort fehlgeschlagen",
                        "Ollama antwortet nicht. Läuft der Dienst?",
                    ),
                )
                return None
        if CFG.ollama_cleanup_enabled:
            self.root.after(0, lambda: self._apply_state("cleaning"))
            try:
                return glossary.apply_replacements(self._cleanup_text(text))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                logger.warning("Ollama cleanup unavailable, using raw transcript: %s", exc)
                self.root.after(0, lambda: self._apply_state("done", "Eingefügt · ohne Bereinigung"))
        return text  # Cleanup deaktiviert oder nicht erreichbar — Roh-Transkript

    def _abliefern(self, text: str) -> None:
        """Einfügen zuerst, Datei im Hintergrund speichern → kein Disk-I/O vor dem Paste."""
        self._add_to_history(text)
        self._copy_and_paste(text)
        threading.Thread(
            target=self._save_transcript_file,
            args=(text,),
            daemon=True,
            name="save-transcript",
        ).start()
        self.root.after(0, lambda: self._apply_state("done"))
