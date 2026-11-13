#!/usr/bin/env bash
# One-time system setup for Hams on Linux (made for Kubuntu / KDE Plasma on Wayland; apt-based).
#
#   sudo bash linux/setup-system.sh                        packages + /dev/uinput permission
#   sudo bash linux/setup-system.sh --with-nvidia-driver   also install the recommended NVIDIA driver
#   sudo bash linux/setup-system.sh --remove               take the /dev/uinput permission back
#   sudo bash linux/setup-system.sh --help
#
# What it does:
#   1. apt-get install python3-tk libportaudio2 wl-clipboard gir1.2-ayatanaappindicator3-0.1
#      (windows, microphone and sound, Wayland clipboard, tray icon)
#   2. only with --with-nvidia-driver: `ubuntu-drivers install` (the driver Ubuntu recommends)
#   3. a udev rule that lets the logged-in user use /dev/uinput, the virtual keyboard Hams pastes with
# Reboot afterwards. Packages and the driver stay installed with --remove; other programs may need them.
#
# Older names still work: --entfernen = --remove. The NVIDIA driver used to be installed by default and
# could be skipped with OHNE_NVIDIA=1 / NO_NVIDIA=1; it is now opt-in (--with-nvidia-driver or
# WITH_NVIDIA_DRIVER=1), and those variables still switch it off.
set -euo pipefail

usage() {
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

mode=install
nvidia_driver="${WITH_NVIDIA_DRIVER:-}"
for arg in "$@"; do
    case "$arg" in
        --remove | --entfernen) mode=remove ;;
        --with-nvidia-driver) nvidia_driver=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (see --help)" >&2
            exit 2
            ;;
    esac
done
# Old opt-out variables: honoured, so a script that set them never gets a driver.
if [[ -n "${NO_NVIDIA:-}${OHNE_NVIDIA:-}" ]]; then
    nvidia_driver=""
fi

if [[ $EUID -ne 0 ]]; then
    echo "Please run it with sudo: sudo bash $0 $*" >&2
    exit 1
fi

RULE=/etc/udev/rules.d/60-hams-uinput.rules

if [[ "$mode" == remove ]]; then
    rm -f "$RULE"
    udevadm control --reload-rules
    udevadm trigger --name-match=uinput || true
    echo "Removed the /dev/uinput permission (Hams can no longer paste)."
    echo "Packages and the NVIDIA driver stay installed; other programs and the display may need them."
    exit 0
fi

if ! command -v apt-get > /dev/null; then
    echo "This script needs apt (Ubuntu, Kubuntu, Debian and derivatives)." >&2
    echo "On other distributions, install Tk for Python, PortAudio, wl-clipboard and an AppIndicator library" >&2
    echo "with your package manager, and add the udev rule from this script by hand." >&2
    exit 1
fi

echo "== Updating package lists"
apt-get update

echo "== Packages for Hams"
# python3-tk: windows; libportaudio2: microphone and sounds; wl-clipboard: clipboard
# on Wayland; Ayatana indicator: icon in the panel's system tray
apt-get install -y python3-tk libportaudio2 wl-clipboard gir1.2-ayatanaappindicator3-0.1

if [[ -n "$nvidia_driver" ]]; then
    echo "== NVIDIA driver (recommended version)"
    if command -v ubuntu-drivers > /dev/null; then
        ubuntu-drivers install
    else
        echo "ubuntu-drivers is not available; install the NVIDIA driver the way your distribution recommends." >&2
    fi
fi

echo "== Permission for the virtual keyboard (/dev/uinput)"
# Hams pastes with Ctrl+V. On Wayland that only works through its own input
# device. 'uaccess' grants it only to the user sitting at the machine, not to
# every account.
cat > "$RULE" << 'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
EOF
udevadm control --reload-rules
udevadm trigger --name-match=uinput || true

echo
echo "Done. Please restart the computer now."
