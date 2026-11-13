#!/usr/bin/env bash
# Installs Hams on Linux from this copy of the repository (made for Kubuntu / KDE Plasma on Wayland; apt-based).
#
#   bash linux/install.sh                 everything below, asking before each optional step
#   bash linux/install.sh --cpu           without NVIDIA's CUDA libraries (no NVIDIA graphics card)
#   bash linux/install.sh --gpu           with them, even if nvidia-smi doesn't find a card yet
#   bash linux/install.sh --skip-system   leave out the system setup (it needs sudo)
#   bash linux/install.sh --yes           answer every question with yes
#   bash linux/install.sh --help
#
# The steps are the ones in docs/user-guide.md#linux:
#   1. sudo bash linux/setup-system.sh: packages for Tk, sound, clipboard and tray, and /dev/uinput access
#   2. uv: used if it is installed, otherwise installed from astral.sh after asking
#   3. .venv with the system Python and --system-site-packages (Tk and the tray library from step 1)
#   4. Hams into .venv, with the [cuda] extra when nvidia-smi finds a graphics card; no development extras
#   5. after asking: menu entry and autostart at login (hams/setup_autostart.py)
# Run it as your normal user, not with sudo; it asks for sudo itself in step 1.
# Running it again updates the installation (for example after `git pull`).
set -euo pipefail

usage() {
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

assume_yes=""
skip_system=""
gpu=auto
for arg in "$@"; do
    case "$arg" in
        --yes | -y) assume_yes=1 ;;
        --skip-system) skip_system=1 ;;
        --cpu) gpu=no ;;
        --gpu) gpu=yes ;;
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

if [[ $EUID -eq 0 ]]; then
    echo "Please run it as your normal user, without sudo: bash $0" >&2
    echo "It asks for sudo itself for the system setup." >&2
    exit 1
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$repo/pyproject.toml" || ! -d "$repo/hams" ]]; then
    echo "This script must stay in the linux/ folder of a Hams checkout ($repo has no pyproject.toml)." >&2
    exit 1
fi
cd "$repo"

# ask "Question?" -> 0 for yes. Without a terminal (and without --yes) the answer is no.
ask() {
    if [[ -n "$assume_yes" ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        return 1
    fi
    local reply
    read -r -p "$1 [y/N] " reply
    [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]]
}

echo "== Installing Hams from $repo"

# 1. System setup ---------------------------------------------------------------------------------
system_done=""
if [[ -n "$skip_system" ]]; then
    echo "== Skipping the system setup (--skip-system)"
else
    echo "== System setup (sudo): packages and /dev/uinput access"
    sudo bash "$repo/linux/setup-system.sh"
    system_done=1
fi

# 2. uv -------------------------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv > /dev/null; then
    echo "uv (https://docs.astral.sh/uv/) installs Hams' Python packages and is not installed yet."
    if ! ask "Install uv now with its official installer (curl -LsSf https://astral.sh/uv/install.sh | sh)?"; then
        echo "Install uv yourself, then run this script again." >&2
        exit 1
    fi
    if ! command -v curl > /dev/null; then
        echo "curl is missing: sudo apt install curl" >&2
        exit 1
    fi
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if ! command -v uv > /dev/null; then
        echo "uv was installed but is not on PATH. Open a new terminal and run this script again." >&2
        exit 1
    fi
fi
echo "== uv $(uv --version | cut -d' ' -f2)"

# 3. Virtual environment --------------------------------------------------------------------------
python=/usr/bin/python3
if [[ ! -x "$python" ]]; then
    echo "$python is missing: sudo apt install python3" >&2
    exit 1
fi
if [[ -x .venv/bin/python ]]; then
    echo "== Using the existing .venv"
else
    echo "== Creating .venv"
    uv venv --system-site-packages --python "$python" .venv
fi

# 4. Hams -----------------------------------------------------------------------------------------
if [[ "$gpu" == auto ]]; then
    gpu=no
    if command -v nvidia-smi > /dev/null && nvidia-smi -L > /dev/null 2>&1; then
        gpu=yes
    fi
fi
if [[ "$gpu" == yes ]]; then
    echo "== Installing Hams with NVIDIA's CUDA libraries"
    uv pip install --python .venv/bin/python -e ".[cuda]"
else
    echo "== Installing Hams for the CPU (use --gpu to add NVIDIA's CUDA libraries)"
    uv pip install --python .venv/bin/python -e .
fi

# 5. Menu entry and autostart ---------------------------------------------------------------------
if ask "Add Hams to the application menu and start it at login? (KDE needs the menu entry for the hotkeys)"; then
    .venv/bin/python hams/setup_autostart.py
else
    echo "Skipped. Later: .venv/bin/python hams/setup_autostart.py (remove with --remove)"
fi

echo
echo "Done."
if [[ -n "$system_done" ]]; then
    echo "Reboot once so the /dev/uinput permission applies, then start Hams from the application menu or with:"
else
    echo "Start Hams from the application menu or with:"
fi
echo "    cd \"$repo\" && .venv/bin/python -m hams"
echo "The setup assistant opens on the first start. Problems: docs/troubleshooting.md#linux"
