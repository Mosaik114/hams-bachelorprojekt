# Whisperflow-by-Nerway — Projekt-Plan

## Status: **Entwicklung (nicht öffentlich)**

---

## Was wurde erledigt ✅

### Grundprojekt
- [x] Wisper als Python-Paket strukturiert (`wisper/` Verzeichnis)
- [x] `pyproject.toml` für pip-Installation erstellt
- [x] MIT LICENSE hinzugefügt
- [x] README.md aktualisiert
- [x] Git Repo initialisiert (lokal)

### Transcription-Problem behoben
- [x] Cleanup-Prompt neu formuliert (strengere Anweisungen gegen Über-Korrektur)
- [x] Cleanup- und Prompt-Modell auf `ministral-3:8b` gesetzt

### Verteilung vorbereitet
- [x] Projekt als pip-Paket vorbereitet
- [x] Entry-Point `wisper` funktioniert

### Phase 1: Code-Qualität ✅
- [x] Config-Validierung hinzugefügt (`wisper/config_validation.py`)
- [x] Logging-Struktur verbessert (DEBUG level, Console + File handler)
- [x] Fehlerbehandlung verbessert (war bereits gut, minimale Anpassungen)
- [x] Docstrings für wichtige Funktionen hinzugefügt

### Phase 2: Testing ✅
- [x] Unit-Tests für Config-Parsing (3 tests)
- [x] Unit-Tests für Config-Validierung (8 tests)
- [x] Unit-Tests für Cleanup-Logik (3 tests)
- [x] CI/CD Pipeline eingerichtet (GitHub Actions)

### Phase 3: Plattform-Reife
- [ ] `pyproject.toml` — GitHub URL von Platzhalter korrigieren
- [ ] PyInstaller-Build für .exe erstellen
- [ ] Linux/macOS Kompatibilität (optional)

### Phase 4: Veröffentlichung
- [x] README erweitert (Entwicklung, Tests, CI/CD Sektion)
- [x] `CHANGELOG.md` erstellt
- [ ] Auf PyPI hochladen (`twine upload dist/*`)
- [ ] GitHub Repo öffentlich machen

### Phase 5: Andere Nutzer
- [x] Troubleshooting-Sektion in README
- [x] Config-Doku in README
- [ ] Installations-Anleitung für Ollama-Modelle (bereits in README)

---

## Projekt-Struktur (aktuell)
```
Wisper/
├── .git/                      # Lokales Git-Repo
├── .github/workflows/ci.yml    # CI/CD Pipeline
├── wisper/                    # Hauptpaket
│   ├── __init__.py
│   ├── main.py                # App-Logik
│   ├── setup_autostart.py     # Windows Autostart
│   ├── config.toml            # Konfiguration
│   ├── config_validation.py   # Config-Validierung
│   └── prompts/
│       ├── __init__.py
│       └── cleanup.txt        # Cleanup-Prompt
├── tests/                     # Unit-Tests (15 tests)
│   ├── conftest.py
│   ├── test_config_validation.py
│   ├── test_config_load.py
│   └── test_cleanup.py
├── LICENSE                    # MIT License
├── README.md                  # Dokumentation
├── CHANGELOG.md               # Änderungsprotokoll
├── pyproject.toml             # pip-Paket-Konfiguration
├── requirements.txt           # Dependencies
├── PROJECT_PLAN.md            # Dieser Plan
└── start_wisper.vbs          # Starter-Skript
```

---

## Nächste Schritte (Reihenfolge)

1. **Config-Validierung einbauen** — Config wird aktuell einfach geparst ohne Validierung
2. **Test-Struktur aufsetzen** — `pytest` + erste Tests
3. **README erweitern** — Screenshots, bessere Doku
4. **GitHub Repo erstellen** — `git remote add origin ...` (wie von dir vorbereitet)
5. **PyPI Upload** — wenn gewünscht

---

## Notizen

-ollama Version: 0.21.1
- Cleanup-Modell: ministral-3:8b
- Prompt-Modell: ministral-3:8b
- Windows-spezifisch (Win32 API, VBScript)
