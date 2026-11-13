"""Aus der Tonspur wird Text: Whisper, Bereinigung mit Ollama, Einfuegen."""

import collections
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pyperclip

if sys.platform == "win32":
    import keyboard
else:
    from hams.linux import keyboard

from hams import cleanup_guard, dictation_language, glossary
from hams import ollama as ollama_api
from hams.backend import BatchedInferencePipeline
from hams.config import CFG, OUTPUT_DIR, logger
from hams.i18n import t, tn
from hams.recording import MODEL_WAIT_SECONDS, SAMPLE_RATE, _normalize_audio

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

#: Fixed Whisper options (docs/evaluation.md, docs/hardware.md). Greedy decoding (`beam_size` 1) is 2-3 times
#: faster than faster-whisper's default of 5 at hardly any loss for dictation; no previous-text conditioning, so a
#: recording never continues an earlier one; no timestamps, which Hams never shows.
WHISPER_OPTIONS = {
    "beam_size": 1,
    "condition_on_previous_text": False,
    "word_timestamps": False,
    "without_timestamps": True,
}

#: The run id of the pipeline job this thread is working on (see `TranscriptionMixin._pipeline_ui`).
_pipeline_run = threading.local()


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


def _prompt_language(language: str) -> str:
    """Die diktierte Sprache, sonst die eingestellte; "" = unbekannt ("auto" ohne Ergebnis).

    Welche Anweisungen daraus folgen, entscheidet `dictation_language` (unbekannt: die englischen).
    """
    language = language or CFG.transcription_language
    return "" if language == dictation_language.AUTO else language


def cleanup_options() -> dict:
    """Sampling fuer die Bereinigung: Temperatur aus der Einstellung (Vorgabe 0), fester Seed.

    Ohne Optionen sampelt Ollama mit den Vorgaben des Modells (qwen3.5: temperature 1). Gemessen
    (docs/evaluation.md) erfindet die Bereinigung bei 0 seltener etwas, formuliert seltener um und liefert
    fuer dasselbe Diktat jedes Mal denselben Text. Nur die Bereinigung; *KI fragen* bleibt bei den Vorgaben.
    """
    return {"temperature": CFG.ollama_cleanup_temperature, "seed": 0}


#: The names `_create_transcript_file` gives: only these files are ever deleted by the retention.
_TRANSCRIPT_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?\.txt")


def prune_transcripts(folder: Path, keep_days: int, now: float | None = None) -> int:
    """Deletes saved transcripts older than `keep_days` days (0 keeps them). Returns how many went.

    Only files named like Hams names them: anything else in the folder belongs to the user.
    """
    if keep_days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - keep_days * 86_400
    try:
        candidates = [entry for entry in folder.iterdir() if _TRANSCRIPT_NAME.fullmatch(entry.name)]
    except OSError:  # no folder yet, or not readable
        return 0
    removed = 0
    for entry in candidates:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            logger.warning("Old transcript %s could not be deleted", entry.name, exc_info=True)
    if removed:
        logger.info("Deleted %d transcript(s) older than %d days", removed, keep_days)
    return removed


def _clipboard_confirms(text: str, timeout: float) -> bool:
    r"""Whether the clipboard holds `text` within `timeout` seconds; asked at least once.

    Line endings are compared as `\n`: Windows hands `\r\n` back for a `\n` that was copied.
    """
    target = text.replace("\r\n", "\n")
    deadline = time.monotonic() + timeout
    while True:
        try:
            if pyperclip.paste().replace("\r\n", "\n") == target:
                return True
        except pyperclip.PyperclipException:
            logger.warning("Clipboard could not be read", exc_info=True)
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


class TranscriptionMixin:
    """Transkription, Bereinigung mit Ollama und Einfuegen des Textes."""

    #: Nach einem Ausfall von Ollama fragt die Bereinigung so lange nicht erneut. Jedes Diktat
    #: wuerde sonst erst auf einen Dienst warten, der nicht da ist.
    CLEANUP_RETRY_SECONDS = 60.0
    #: Klassenwerte als Vorgabe: Tests bauen die Anwendung auch ohne __init__.
    _cleanup_retry_at = 0.0
    _cleanup_offline_noted = False
    #: (Statustext, Hinweis) fuer die naechste Ablieferung, oder None.
    _delivery_note: tuple | None = None

    # ------------------------------------------------------------------ transcription

    def _generate_from_prompt(self, text: str, language: str = "") -> str:
        """Die KI-Antwort zum diktierten Prompt — ebenfalls als Klartext.

        Auch diese Antwort wird unverändert eingefügt; ein `**Oslo**` im
        Textfeld ist dort genauso unerwünscht wie im korrigierten Transkript.
        Zwei Ebenen wie beim Cleanup: eine Systemanweisung an Ollama und
        dieselbe Normalisierung danach. Der diktierte Prompt selbst bleibt
        unangetastet.

        Absätze und Zeilenumbrüche bleiben erhalten — im Prompt-Modus tragen
        sie Bedeutung, anders als eine Hervorhebung.

        `language` ist die diktierte Sprache (leer: die eingestellte); sie waehlt die Systemanweisung.
        """
        answer = self.ollama.generate(
            text,
            CFG.ollama_model_prompt,
            timeout=CFG.ollama_timeout_prompt,
            keep_alive=CFG.ollama_keep_alive,
            system=dictation_language.plain_system(_prompt_language(language)),
            on_progress=self._show_answer_length,  # long answers: the window counts the characters as they arrive
        )
        answer = plain_text(answer)
        if not answer.strip():
            raise ollama_api.OllamaAnswerError("Ollama gave an empty answer")
        return answer

    def _show_answer_length(self, characters: int) -> None:
        self._pipeline_ui(lambda: self.elapsed_label.config(text=tn("status.characters", characters)))

    def _cleanup_text(self, text: str, language: str = "") -> str:
        """Clean up transcript text using LLM.

        Removes stuttering, repetitions, and slips of the tongue.
        Corrects spelling and grammar while preserving original phrasing.

        Die Antwort geht durch `plain_text()`, bevor sie irgendwohin fliesst.
        Der Prompt verbietet Markdown, aber ein Modell haelt sich nicht immer
        daran — und was hier durchrutscht, landet als sichtbares `**` im
        Eingabefeld des Nutzers.

        Danach prueft `cleanup_guard.check` die Antwort: ein wiederholter Prompt, veraenderte Ziffern, eine
        viel laengere Antwort oder eine, die die meisten Woerter verliert, wird durch den Rohtext ersetzt.

        `language` ist die diktierte Sprache (leer: die eingestellte); sie waehlt Anweisung und Glossar-Satz.
        """
        language = _prompt_language(language)
        template = dictation_language.cleanup_template(language, CFG.cleanup_prompt_template)
        prompt = template.replace("{text}", text) if "{text}" in template else f"{template}\n\nText: {text}"
        instructions = template
        if hinweis := glossary.cleanup_hint(language):
            prompt = f"{hinweis}\n\n{prompt}"
            instructions = f"{hinweis}\n\n{template}"
        answer = plain_text(
            self.ollama.generate(
                prompt,
                CFG.ollama_model_cleanup,
                timeout=CFG.ollama_timeout_cleanup,
                keep_alive=CFG.ollama_keep_alive,
                options=cleanup_options(),
            )
        )
        result = cleanup_guard.check(text, answer, instructions)
        if result.reason:
            # Nie den Text loggen, nur Grund und Laengen.
            logger.warning(
                "Cleanup answer adjusted (%s): %d chars in, %d chars out, %d chars pasted",
                result.reason,
                len(text),
                len(answer),
                len(result.text),
            )
        return result.text

    def _save_transcript_file(self, text: str) -> None:
        """Save transcript to a timestamped file in the transcripts directory, then apply the retention.

        Two dictations within the same second get their own files: the name is created exclusively and a
        counter is added when it is taken, so nothing is ever overwritten.
        """
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_file = self._create_transcript_file(stamp, text)
            logger.info("Saved transcript: %s (%d chars)", out_file.name, len(text))
        except OSError:
            logger.exception("Failed to save transcript file")
        prune_transcripts(OUTPUT_DIR, CFG.transcripts_keep_days)

    def _prune_transcripts_async(self) -> None:
        """At start: delete transcripts past `[output] keep_days`, also when saving is off now."""
        if CFG.transcripts_keep_days > 0:
            threading.Thread(
                target=prune_transcripts,
                args=(OUTPUT_DIR, CFG.transcripts_keep_days),
                daemon=True,
                name="prune-transcripts",
            ).start()

    @staticmethod
    def _create_transcript_file(stamp: str, text: str, attempts: int = 1000):
        """Writes `text` to `<stamp>.txt`, or `<stamp>_2.txt` and so on if that name exists."""
        for number in range(1, attempts + 1):
            out_file = OUTPUT_DIR / (f"{stamp}.txt" if number == 1 else f"{stamp}_{number}.txt")
            try:
                with out_file.open("x", encoding="utf-8") as handle:
                    handle.write(text)
            except FileExistsError:
                continue
            return out_file
        raise FileExistsError(f"no free transcript name for {stamp}")

    def _warmup_ollama_async(self) -> None:
        threading.Thread(target=self._warmup_ollama, daemon=True, name="ollama-warmup").start()

    def _warmup_ollama(self) -> None:
        """Lädt das Cleanup-Modell proaktiv in den VRAM, damit der erste
        echte Request nicht mit einem Kaltstart von 10–30s büßt."""
        if not CFG.ollama_cleanup_enabled:
            return
        model = CFG.ollama_model_cleanup
        try:
            self.ollama.warm_up(model, CFG.ollama_keep_alive)
            logger.info("Ollama warmed up: %s", model)
        except Exception as exc:  # noqa: BLE001 - Grenze des Threads: Vorwaermen ist nur ein Versuch
            logger.warning("Ollama warmup failed for %s: %s", model, exc)

    def _copy_and_paste(self, text: str) -> bool:
        """Copies the text and sends Ctrl+V - but only once the clipboard confirms it holds that text.

        Without the confirmation Ctrl+V would paste whatever was on the clipboard before (a password, say), so
        Hams pastes nothing and asks for Ctrl+V instead; the text is in the tray history either way. Returns
        whether it pasted.
        """
        pyperclip.copy(text)
        if _clipboard_confirms(text, CFG.clipboard_paste_timeout):
            keyboard.send("ctrl+v")
            return True
        logger.warning("Clipboard did not confirm the text within %.2f s - not pasted", CFG.clipboard_paste_timeout)
        self._delivery_note = (t("status.copied_only"), t("hint.press_ctrl_v"))
        return False

    def _run_transcription(self, model, audio: np.ndarray) -> tuple[str, str] | None:
        """Fuehrt Whisper mit einer *festen* Modellreferenz aus.

        Bewusst getrennt, damit der Aufrufer den Transkriptionslock nur um
        diesen Teil legen muss — Cleanup und Einfuegen brauchen ihn nicht und
        wuerden einen Modellwechsel unnoetig lange blockieren.

        Gibt Text und diktierte Sprache zurueck ("" = unbekannt), None nach
        einem Fehler (gemeldet).
        """
        try:
            normalised = _normalize_audio(audio)
            language = self._dictation_language(model, normalised)
            optionen = {
                "language": language,
                "vad_filter": CFG.transcription_vad_filter,
                **WHISPER_OPTIONS,
                # Glossar: Namen und Begriffe öfter richtig erkennen
                "hotwords": glossary.hotwords(language),
            }
            dauer = audio.size / SAMPLE_RATE
            if dauer >= BATCHED_MIN_SECONDS:
                # The batched pipeline transcribes the speech chunks independently; faster-whisper ignores
                # `condition_on_previous_text` there. The sequential path has it off as well (WHISPER_OPTIONS),
                # so both paths treat every chunk without the text before it.
                segments, info = BatchedInferencePipeline(model=model).transcribe(
                    normalised, batch_size=BATCH_SIZE, **optionen
                )
            else:
                segments, info = model.transcribe(normalised, **optionen)
            # Raw text: the glossary replacements come once, on the final text (see `_nachbearbeiten`).
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return text, language or str(getattr(info, "language", "") or "")
        except Exception:
            logger.exception("Whisper transcription failed")
            self._pipeline_ui(lambda: self._apply_state("error", t("error.transcription_failed"), t("hint.see_log")))
            return None

    @staticmethod
    def _dictation_language(model, audio: np.ndarray) -> str | None:
        """Die Sprache fuer diese Aufnahme: die eingestellte oder, bei "auto", die erkannte.

        Bei "auto" erkennt Whisper die Sprache *vor* der Transkription, damit Rahmensatz und Hinweise
        schon zur Sprache passen. Kann das Modell das nicht (oder findet es nichts, etwa in einer
        Aufnahme ohne Sprache), erkennt `transcribe` sie selbst — dann ohne Glossar-Hinweis.
        """
        language = dictation_language.whisper_language(CFG.transcription_language)
        if language is not None:
            return language
        detect = getattr(model, "detect_language", None)
        if detect is None:
            return None
        try:
            detected, probability, _ = detect(audio=audio, vad_filter=CFG.transcription_vad_filter)
        except Exception as exc:
            logger.info("Language detection found nothing (%s), Whisper decides while transcribing", type(exc).__name__)
            return None
        logger.info("Detected language: %s (p=%.2f)", detected, probability)
        return detected

    # ------------------------------------------------------------------ pipeline queue
    #
    # A recording may start while the previous one is still being transcribed or cleaned up (the hotkeys
    # stay live). Every finished recording becomes a job with its own prompt mode and run id; one worker
    # thread works through the jobs in order, so each text is pasted, in the order it was spoken, and none
    # is lost. Only the newest run may change the main window, and only while nothing is being recorded:
    # an older job finishing late must not overwrite the recording or the state of a newer one.

    #: Class defaults: tests also build the app without __init__.
    _pipeline_latest = 0
    _pipeline_busy = False
    #: True while the worker transcribes a job it has taken from the queue.
    _pipeline_job_running = False
    _pipeline_jobs: collections.deque | None = None
    _pipeline_lock = threading.Lock()

    def _next_pipeline_run(self) -> int:
        """A new run id; from now on older runs no longer touch the main window. On the Tk thread."""
        self._pipeline_latest += 1
        return self._pipeline_latest

    def _enqueue_transcription(self, audio: np.ndarray, prompt_mode: bool, run_id: int) -> None:
        """Queues a finished recording. Starts the worker unless one is already working through the queue."""
        with self._pipeline_lock:
            if self._pipeline_jobs is None:
                self._pipeline_jobs = collections.deque()
            self._pipeline_jobs.append((audio, prompt_mode, run_id))
            if self._pipeline_busy:
                logger.info("Transcription queued behind the running one (run %d)", run_id)
                return
            self._pipeline_busy = True
        threading.Thread(target=self._drain_pipeline, daemon=True, name="transcribe").start()

    def _drain_pipeline(self) -> None:
        """The worker: one job after the other, in the order they were recorded; ends when the queue is empty."""
        while True:
            with self._pipeline_lock:
                if not self._pipeline_jobs:
                    self._pipeline_busy = False
                    self._pipeline_job_running = False
                    return
                audio, prompt_mode, run_id = self._pipeline_jobs.popleft()
                self._pipeline_job_running = True
            try:
                self._transcribe_audio(audio, prompt_mode=prompt_mode, run_id=run_id)
            except Exception:  # noqa: BLE001 - Grenze des Threads: der naechste Auftrag muss trotzdem laufen
                logger.exception("Transcription job crashed")

    def _pipeline_ui(self, fn) -> None:
        """A main-window update from the pipeline: applied only for the current run and not while recording."""
        run_id = getattr(_pipeline_run, "run_id", None)

        def guarded() -> None:
            if getattr(self, "is_recording", False):
                return
            if run_id is not None and run_id != self._pipeline_latest:
                return
            fn()

        self._ui_call(guarded)

    def _transcribe_audio(self, audio: np.ndarray, prompt_mode: bool = False, run_id: int | None = None) -> None:
        """Transcription pipeline thread target.

        Runs Whisper transcription, optionally cleans up via LLM,
        saves to file, copies to clipboard, and pastes at cursor.

        `prompt_mode` belongs to this recording; it is passed in, never read from shared state, because the
        next recording may already have started. `run_id` decides whether this run may still change the
        main window (default: the newest run).
        """
        _pipeline_run.run_id = self._pipeline_latest if run_id is None else run_id
        try:
            self._transcribe_job(audio, prompt_mode)
        finally:
            _pipeline_run.run_id = None

    def _transcribe_job(self, audio: np.ndarray, prompt_mode: bool) -> None:
        if not self.model_ready.is_set() and not self._auf_modell_warten():
            return
        # Genau einmal eine stabile Referenz holen und den Lauf damit zu Ende
        # bringen; der Lock haelt einen Modellwechsel so lange zurueck.
        with self._transcribe_lock:
            model = self.current_model()
            if model is None:
                self._pipeline_ui(lambda: self._apply_state("error", t("error.no_model"), t("hint.try_again")))
                return
            result = self._run_transcription(model, audio)
        if result is None:
            return
        text, language = result
        try:
            if not text:
                logger.info("Transcription returned empty text")
                self._pipeline_ui(
                    lambda: self._apply_state(
                        "no_signal",
                        t("status.no_speech"),
                        t("hint.no_words"),
                    ),
                )
                return
            logger.info("Transcribed %d chars (prompt_mode=%s)", len(text), prompt_mode)
            text = self._nachbearbeiten(text, prompt_mode, language)
            if text is not None:
                self._abliefern(text)
        except Exception:
            logger.exception("Transcription pipeline failed")
            self._pipeline_ui(
                lambda: self._apply_state(
                    "error",
                    t("error.transcription_failed"),
                    t("hint.see_log"),
                ),
            )

    def _auf_modell_warten(self) -> bool:
        """Die Aufnahme durfte ohne Modell beginnen; hier wird es faellig. In aller Regel steht es laengst — der
        Nutzer hat waehrend des Ladens gesprochen. Nur der erste Griff kurz nach dem Anmelden wartet hier wirklich,
        und dann sagt die Statuszeile auch, worauf. False = kein Modell (Fehler ist schon gemeldet)."""
        logger.info("Aufnahme wartet auf das Modell")
        self._pipeline_ui(
            lambda: self._apply_state(
                "transcribing",
                t("status.model_still_loading"),
                t("hint.recording_kept"),
            ),
        )
        if not self._await_model(time.monotonic() + MODEL_WAIT_SECONDS):
            logger.error("Kein Modell fuer eine fertige Aufnahme verfuegbar")
            self._pipeline_ui(
                lambda: self._apply_state(
                    "error",
                    t("error.model_unavailable"),
                    t("error.recording_not_transcribed"),
                ),
            )
            return False
        self._pipeline_ui(lambda: self._apply_state("transcribing"))
        return True

    def _nachbearbeiten(self, text: str, was_prompt_mode: bool, language: str = "") -> str | None:
        """Prompt-Modus: KI-Antwort statt Diktat; sonst (falls an) Bereinigung. None = abgebrochen (gemeldet).

        `language` ist die diktierte Sprache; sie waehlt die Anweisungen an Ollama.

        The glossary replacements apply exactly once, to the dictated text that ends up final: the pasted
        transcript (cleaned or raw) or, in prompt mode, the prompt sent to the AI. The cleanup itself gets
        the raw text plus the glossary hint, so a replacement is never applied twice.
        """
        if was_prompt_mode:
            return self._answer_prompt(glossary.apply_replacements(text), language)
        return glossary.apply_replacements(self._cleaned_or_raw(text, language))

    def _answer_prompt(self, text: str, language: str) -> str | None:
        """The AI's answer to the dictated prompt; None after a clear "AI failed" message."""
        self._pipeline_ui(lambda: self._apply_state("generating"))
        try:
            return self._generate_from_prompt(text, language)
        except ollama_api.OLLAMA_ERRORS as exc:
            logger.exception("Ollama prompt-generation failed")
            unusable = isinstance(exc, ollama_api.OllamaAnswerError)
            hint = t("error.ai_no_answer") if unusable else t("error.ollama_not_responding")
            self._pipeline_ui(lambda: self._apply_state("error", t("error.ai_failed"), hint))
            return None

    def _cleaned_or_raw(self, text: str, language: str) -> str:
        """The cleaned-up transcript, or the raw one if cleanup is off or Ollama fails in any way."""
        if not CFG.ollama_cleanup_enabled:
            return text
        if time.monotonic() < self._cleanup_retry_at:
            self._delivery_note = (t("status.done_raw"), None)  # gerade erst ausgefallen: nicht erneut warten
            return text
        self._pipeline_ui(lambda: self._apply_state("cleaning"))
        try:
            return self._cleanup_text(text, language)
        except ollama_api.OLLAMA_ERRORS as exc:
            if ollama_api.is_unreachable(exc):
                logger.warning("Ollama cleanup unavailable, using raw transcript: %s", type(exc).__name__)
                self._note_cleanup_offline()
            else:
                # Ollama answered, just not usably: this dictation goes out raw, the next one asks again.
                logger.warning("Ollama cleanup answer unusable, using raw transcript: %s", type(exc).__name__)
                self._delivery_note = (t("status.done_raw"), None)
        return text

    def _note_cleanup_offline(self) -> None:
        """Ollama fehlt: Rohtext einfuegen, einmal je Sitzung ruhig erklaeren, danach nur noch kurz."""
        hint = None if self._cleanup_offline_noted else t("hint.cleanup_offline")
        self._cleanup_offline_noted = True
        self._cleanup_retry_at = time.monotonic() + self.CLEANUP_RETRY_SECONDS
        self._delivery_note = (t("status.done_raw"), hint)

    def _abliefern(self, text: str) -> None:
        """Paste first; the file (only with `[output] save_transcripts`) is written in the background afterwards."""
        self._add_to_history(text)
        self._copy_and_paste(text)
        if CFG.save_transcripts:
            threading.Thread(
                target=self._save_transcript_file,
                args=(text,),
                daemon=True,
                name="save-transcript",
            ).start()
        detail, hint = self._delivery_note or (None, None)
        self._delivery_note = None
        self._pipeline_ui(lambda: self._apply_state("done", detail, hint, calm=True))
