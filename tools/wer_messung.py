"""Wortfehlerrate messen — damit „schneller" nicht heimlich „ungenauer" heisst.

Wozu
----
Jede Beschleunigung der Transkription steht im Verdacht, Genauigkeit zu
kosten: eine andere Rechenart, ein anderes Modell, ein anderer Ablauf. Ohne
Zahl bleibt es beim Bauchgefuehl. Dieses Skript liefert die Zahl.

Es gehoert bewusst *nicht* in die Testsuite: es braucht eine GPU, die Windows-
Sprachausgabe und je nach Modell Minuten. `pytest tests/` soll in unter einer
Minute durchlaufen.

Wie
---
Windows' eigene Sprachausgabe spricht zwanzig deutsche Saetze, deren Wortlaut
bekannt ist. Daraus entstehen vier Bedingungen — sauber und mit Stoergeraeusch
bei 20, 10 und 5 dB Stoerabstand. Quantisierung schlaegt, wenn ueberhaupt, bei
schlechtem Signal durch; ein Vergleich nur auf sauberem Ton waere zu freundlich.

Was die Zahlen bedeuten
-----------------------
Die absolute Fehlerrate gilt fuer *synthetische* Sprache und ist nicht auf
echte uebertragbar. Ein grosser Teil davon sind ohnehin Zahlwoerter: gesprochen
"null eins fuenf eins", geschrieben "0151". Aussagekraeftig ist allein die
*Differenz* zwischen zwei Konfigurationen auf identischem Audio.

Aufruf
------
    python tools/wer_messung.py                      # float16 gegen int8_float16
    python tools/wer_messung.py --modell small turbo # zwei Modellgroessen
    python tools/wer_messung.py --gebatcht           # sequentiell gegen gebatcht

Gemessene Ergebnisse, RTX 5070, Modell turbo
--------------------------------------------
    float16 -> int8_float16   +0,11 pp WER, 8-12 % schneller
        => nicht uebernommen: schneller, aber messbar ungenauer.
    sequentiell -> gebatcht   -2,38 pp WER bei 48 s Audio, 26 % schneller
        => uebernommen, siehe BATCHED_MIN_SECONDS in main.py.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import subprocess
import sys
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wisper.models import preload_backend_without_torch  # noqa: E402

SAETZE = [
    "Bitte schick mir die Unterlagen bis Freitag an meine Geschäftsadresse.",
    "Der Termin verschiebt sich auf den siebzehnten März um vierzehn Uhr dreißig.",
    "Wir haben im letzten Quartal einen Umsatz von dreiundzwanzig Millionen Euro erreicht.",
    "Kannst du bitte die Rechnungsnummer 4 7 9 2 im System nachschlagen?",
    "Die Spracherkennung funktioniert erstaunlich gut, auch bei Fachbegriffen.",
    "Ich brauche eine Zusammenfassung der Sitzungsprotokolle vom vergangenen Donnerstag.",
    "Der Datenschutzbeauftragte muss dem Verfahren ausdrücklich zustimmen.",
    "Häufig scheitert es nicht an der Technik, sondern an der Abstimmung zwischen den Abteilungen.",
    "Bitte übertrag die Werte aus der Tabelle in die Präsentation für morgen früh.",
    "Die Straßenbahn fährt alle zwölf Minuten vom Hauptbahnhof zum Universitätsklinikum.",
    "Wir sollten die Schnittstelle so bauen, dass sie auch später noch erweiterbar bleibt.",
    "Meine Telefonnummer lautet null eins fünf eins, zwo drei vier fünf sechs.",
    "Über achtzig Prozent der Nutzerinnen und Nutzer verwenden die Anwendung täglich.",
    "Die Qualitätssicherung hat drei kritische Abweichungen im Prüfbericht vermerkt.",
    "Können wir den Auftrag noch vor Jahresende abschließen, oder wird das zu knapp?",
    "Der Wirtschaftsprüfer benötigt die Belege für den Zeitraum Januar bis einschließlich Juni.",
    "Ich hätte gerne einen Rückruf, sobald die Angelegenheit geklärt ist.",
    "Die Übergabe erfolgt am Montag im Besprechungsraum im dritten Obergeschoss.",
    "Bitte berücksichtige, dass die Lieferfrist derzeit bei sechs bis acht Wochen liegt.",
    "Das Verfahren ist zwar aufwendig, aber es liefert nachvollziehbare Ergebnisse.",
]

STOERABSTAENDE = (20, 10, 5)


def korpus_erzeugen(ziel: Path) -> None:
    """Spricht die Saetze ueber die Windows-Sprachausgabe ein."""
    ziel.mkdir(parents=True, exist_ok=True)
    if all((ziel / f"s{i:02d}.wav").exists() for i in range(len(SAETZE))):
        return
    zeilen = ["Add-Type -AssemblyName System.Speech",
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
              "$s.SelectVoice('Microsoft Hedda Desktop')"]
    for i, satz in enumerate(SAETZE):
        zeilen += [f"$s.Rate = {(-2, 0, 2)[i % 3]}",
                   f"$s.SetOutputToWaveFile('{ziel / f's{i:02d}.wav'}')",
                   "$s.Speak(@'\n" + satz + "\n'@)"]
    zeilen += ["$s.SetOutputToNull(); $s.Dispose()"]
    subprocess.run(["powershell", "-NoProfile", "-Command", "\n".join(zeilen)],
                   check=True, capture_output=True)


def laden(pfad: Path) -> np.ndarray:
    with wave.open(str(pfad), "rb") as datei:
        roh = datei.readframes(datei.getnframes())
        kanaele, rate = datei.getnchannels(), datei.getframerate()
    audio = np.frombuffer(roh, dtype=np.int16).astype(np.float32) / 32768.0
    if kanaele > 1:
        audio = audio.reshape(-1, kanaele).mean(axis=1)
    if rate != 16000:
        anzahl = int(round(audio.size * 16000 / rate))
        audio = np.interp(np.linspace(0, audio.size - 1, anzahl),
                          np.arange(audio.size), audio).astype(np.float32)
    return audio


def mit_rauschen(audio: np.ndarray, stoerabstand_db: int, saat: int) -> np.ndarray:
    rng = np.random.default_rng(saat)
    leistung = float(np.mean(audio ** 2)) / (10 ** (stoerabstand_db / 10))
    gestoert = audio + rng.standard_normal(audio.size).astype(np.float32) * np.sqrt(leistung)
    return (gestoert / max(np.max(np.abs(gestoert)), 1e-9) * 0.9).astype(np.float32)


def normalisieren(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    return [w for w in re.sub(r"[^\wäöüß ]+", " ", text).split() if w]


def wortabstand(referenz: list[str], hypothese: list[str]) -> int:
    """Levenshtein auf Wortebene — Einfuegen, Loeschen, Ersetzen zaehlen gleich."""
    vorige = list(range(len(hypothese) + 1))
    for i, r in enumerate(referenz, 1):
        aktuelle = [i]
        for j, h in enumerate(hypothese, 1):
            aktuelle.append(min(vorige[j] + 1, aktuelle[j-1] + 1, vorige[j-1] + (r != h)))
        vorige = aktuelle
    return vorige[-1]


def proben(korpus: Path) -> list[tuple[str, str, str, np.ndarray]]:
    """Alle Bedingungen einmal vorbereiten — beide Laeufe hoeren dasselbe."""
    ergebnis = []
    for i, satz in enumerate(SAETZE):
        audio = laden(korpus / f"s{i:02d}.wav")
        ergebnis.append(("sauber", f"s{i:02d}", satz, audio))
        for db in STOERABSTAENDE:
            ergebnis.append((f"{db} dB", f"s{i:02d}", satz,
                             mit_rauschen(audio, db, saat=1000 * i + db)))
    return ergebnis


def durchlauf(modell, stichproben, gebatcht: bool, batch_size: int):
    from wisper.main import CFG

    optionen = {
        "language": CFG.transcription_language,
        "vad_filter": CFG.transcription_vad_filter,
        "word_timestamps": CFG.transcription_word_timestamps,
        "beam_size": CFG.transcription_beam_size,
        "condition_on_previous_text": CFG.transcription_condition_on_previous_text,
        "without_timestamps": CFG.transcription_without_timestamps,
    }
    if gebatcht:
        from faster_whisper import BatchedInferencePipeline
        laufer = BatchedInferencePipeline(model=modell)
        optionen["batch_size"] = batch_size
    else:
        laufer = modell

    je_bedingung: dict[str, list[int]] = {}
    zeiten, texte = [], {}
    for bedingung, kennung, referenz, audio in stichproben:
        start = time.perf_counter()
        segmente, _ = laufer.transcribe(audio, **optionen)
        hypothese = " ".join(s.text.strip() for s in segmente).strip()
        zeiten.append(time.perf_counter() - start)
        ref, hyp = normalisieren(referenz), normalisieren(hypothese)
        fehler, woerter = je_bedingung.setdefault(bedingung, [0, 0])
        je_bedingung[bedingung] = [fehler + wortabstand(ref, hyp), woerter + len(ref)]
        texte[(bedingung, kennung)] = hypothese
    return {"je_bedingung": je_bedingung, "p50": st.median(zeiten),
            "gesamtzeit": sum(zeiten), "texte": texte}


def gesamt_wer(ergebnis) -> float:
    fehler = sum(f for f, _ in ergebnis["je_bedingung"].values())
    woerter = sum(w for _, w in ergebnis["je_bedingung"].values())
    return fehler / woerter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modell", nargs="+", default=["turbo"])
    parser.add_argument("--rechenart", nargs="+", default=["float16", "int8_float16"])
    parser.add_argument("--geraet", default="cuda")
    parser.add_argument("--gebatcht", action="store_true",
                        help="sequentiell gegen gebatcht statt zwei Rechenarten")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--korpus", type=Path,
                        default=Path(__file__).resolve().parent / "_korpus")
    args = parser.parse_args()

    print("Korpus wird vorbereitet …")
    korpus_erzeugen(args.korpus)
    stichproben = proben(args.korpus)
    print(f"{len(stichproben)} Proben ({len(SAETZE)} Sätze × "
          f"{1 + len(STOERABSTAENDE)} Bedingungen)\n")

    preload_backend_without_torch()
    from faster_whisper import WhisperModel

    laeufe = {}
    for modell_id in args.modell:
        varianten = ([(False, "sequentiell"), (True, f"gebatcht ({args.batch_size})")]
                     if args.gebatcht else
                     [(False, rechenart) for rechenart in args.rechenart])
        for rechenart in (args.rechenart[:1] if args.gebatcht else args.rechenart):
            modell = WhisperModel(modell_id, device=args.geraet, compute_type=rechenart)
            for gebatcht, name in (varianten if args.gebatcht else [(False, rechenart)]):
                schluessel = f"{modell_id}/{name}"
                laeufe[schluessel] = durchlauf(modell, stichproben, gebatcht, args.batch_size)
                spalten = "  ".join(
                    f"{bed} {100*f/w:5.2f}%"
                    for bed, (f, w) in laeufe[schluessel]["je_bedingung"].items())
                print(f"{schluessel:<28} {spalten}   | p50 {laeufe[schluessel]['p50']:.3f}s")
            del modell

    namen = list(laeufe)
    if len(namen) < 2:
        return 0
    grund, *weitere = namen
    print(f"\nGegenüber {grund}:")
    for name in weitere:
        a, b = laeufe[grund], laeufe[name]
        gleich = sum(1 for k in a["texte"] if a["texte"][k] == b["texte"][k])
        print(f"  {name:<26} WER {100*(gesamt_wer(b)-gesamt_wer(a)):+6.2f} pp   "
              f"Zeit {100*(1-b['p50']/a['p50']):+6.1f} %   "
              f"zeichengleich {gleich}/{len(a['texte'])}")
    print("\nEine positive WER-Differenz heisst: ungenauer. Beschleunigungen mit "
          "positiver Differenz\ngehören nicht übernommen, auch wenn sie klein aussehen.")
    (args.korpus / "letztes_ergebnis.json").write_text(json.dumps(
        {k: {"wer": gesamt_wer(v), "p50": v["p50"]} for k, v in laeufe.items()},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
