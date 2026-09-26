# Hams – Stand und Pläne

Hams (Paketname `wisper`) ist Teil von [Mosaiks-Desktop](../README.md) und nur
für den eigenen Rechner gedacht. Aufbau, Einrichtung und Fehlersuche stehen im
[README](README.md), die Änderungen im [CHANGELOG](CHANGELOG.md).

## Erledigt

- Paket mit `pyproject.toml`, Start über `start_wisper.vbs` und Autostart
- Prüfung der Einstellungen, Protokoll ohne diktierte Inhalte
- Code in kleine Module zerlegt (keine Datei über ~700 Zeilen)
- Über 1500 Tests, 91 % Abdeckung, CI verlangt mindestens 85 %
- Feste Paketversionen mit Prüfsumme, `pip-audit` und Dependabot
- Ollama-Adresse: unverschlüsselt nur auf dem eigenen Rechner

## Offen (optional)

- Eigenständige `.exe` mit PyInstaller, damit Hams ohne Python-Installation läuft
- Linux/macOS: nicht geplant, Hams nutzt Win32-Schnittstellen und VBScript
- Veröffentlichung auf PyPI: nicht geplant, das Repo ist privat

## Notizen

- Bereinigungs- und Prompt-Modell: `ministral-3:8b`
- Ollama unter `http://localhost:11434` (Standard)
- Nur Windows
