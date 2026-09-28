#!/usr/bin/env bash
# Einmalig mit Adminrechten: was Hams unter Kubuntu (KDE, Wayland) vom System braucht.
#
#   sudo bash system-einrichten.sh
#   sudo bash system-einrichten.sh --entfernen   Tastatur-Recht zurücknehmen (Pakete und Treiber bleiben)
#
# Danach neu starten. Jeder Schritt ist einzeln abschaltbar:
#   OHNE_NVIDIA=1 sudo -E bash system-einrichten.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Bitte mit sudo starten: sudo bash $0" >&2
    exit 1
fi

if [[ "${1:-}" == "--entfernen" ]]; then
    rm -f /etc/udev/rules.d/60-hams-uinput.rules
    udevadm control --reload-rules
    udevadm trigger --name-match=uinput || true
    echo "Recht auf /dev/uinput zurückgenommen (Hams kann dann nicht mehr einfügen)."
    echo "Pakete und NVIDIA-Treiber bleiben – sie brauchen auch andere Programme und das Bild."
    exit 0
fi

echo "== Paketlisten aktualisieren"
apt-get update

echo "== Pakete für Hams"
# python3-tk: Fenster; libportaudio2: Mikrofon und Töne; wl-clipboard: Zwischenablage
# unter Wayland; Ayatana-Indicator: Symbol im Systemabschnitt der Kontrollleiste
apt-get install -y python3-tk libportaudio2 wl-clipboard gir1.2-ayatanaappindicator3-0.1

if [[ -z "${OHNE_NVIDIA:-}" ]]; then
    echo "== NVIDIA-Treiber (empfohlene Version)"
    ubuntu-drivers install
fi

echo "== Recht auf die virtuelle Tastatur (/dev/uinput)"
# Hams fügt Text mit Strg+V ein. Unter Wayland geht das nur über ein eigenes
# Eingabegerät. 'uaccess' gibt das Recht nur dem, der gerade am Rechner
# angemeldet ist, und nicht allen Benutzern.
cat > /etc/udev/rules.d/60-hams-uinput.rules <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
EOF
udevadm control --reload-rules
udevadm trigger --name-match=uinput || true

echo
echo "Fertig. Bitte jetzt den Rechner neu starten."
