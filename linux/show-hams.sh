#!/usr/bin/env bash
# Hams-Fenster nach vorn holen; läuft Hams nicht, wird es gestartet (Knopf im Kontrollzentrum).
# Zum Beispiel als ~/.local/bin/show-hams verlinken und auf einen Knopf oder Kurzbefehl legen.
#
# Hams schreibt seine Prozessnummer in die Sperrdatei und holt bei SIGUSR1 sein Fenster
# nach vorn (hams/linux.py: acquire_lock, on_show_request).
set -euo pipefail

dienst=app-de.mosaik.Hams.service
sperre="${XDG_RUNTIME_DIR:-/tmp}/hams.lock"

if ! systemctl --user is-active -q "$dienst"; then
    systemctl --user start "$dienst"
    exit 0
fi
pid=$(head -n1 "$sperre" 2>/dev/null || true)
# Nur an einen Hams-Prozess senden: eine ältere Fassung ohne Nummer in der Sperrdatei
# würde SIGUSR1 nicht kennen und sich beenden.
if [[ "$pid" =~ ^[0-9]+$ ]] && grep -qaE "(hams|wisper)/main.py" "/proc/$pid/cmdline" 2>/dev/null; then
    kill -USR1 "$pid"
else
    notify-send -a Hams -i de.mosaik.Hams "Hams" "Bitte Hams einmal neu starten, dann öffnet dieser Knopf das Fenster."
fi
