#!/usr/bin/env bash
# SonusDeck installer.
#
# One-line install (also updates an existing install):
#
#     curl -fsSL https://raw.githubusercontent.com/T3lluz/SonusDeck/main/install.sh | bash
#
# Uninstall:
#
#     curl -fsSL https://raw.githubusercontent.com/T3lluz/SonusDeck/main/install.sh | bash -s -- uninstall
#
# What it does:
#   1. Installs system dependencies with your package manager
#      (python + PyQt6 + PipeWire tools; EasyEffects is optional).
#   2. Clones (or updates) the repo into ~/.local/share/sonusdeck/app.
#   3. Falls back to a private virtualenv for PyQt6 when the distro
#      doesn't ship it.
#   4. Puts `sonusdeck` and `sonusdeck-toggle` launchers in ~/.local/bin.
#   5. Creates the Game / Chat / Media / Aux sinks and tames EasyEffects
#      (no second EQ on the mix, no re-grabbing of assigned streams).
#   6. Starts the panel. First launch registers the Ctrl+Alt+V shortcut,
#      the desktop entry, and start-on-login (toggle it in Settings).

set -euo pipefail

REPO_URL="https://github.com/T3lluz/SonusDeck"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
APP_HOME="$DATA_HOME/sonusdeck"
APP_DIR="$APP_HOME/app"
VENV_DIR="$APP_HOME/venv"
BIN_DIR="$HOME/.local/bin"
BRANCH="${SONUSDECK_BRANCH:-main}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- uninstall

uninstall() {
    log "Removing SonusDeck"
    "$BIN_DIR/sonusdeck" --quit >/dev/null 2>&1 || true
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now sonusdeck.service >/dev/null 2>&1 || true
    fi
    # Stop a leftover channel graph if the panel didn't take it down.
    if [ -f "$CONFIG_HOME/sonusdeck/graph.pid" ]; then
        kill "$(cat "$CONFIG_HOME/sonusdeck/graph.pid")" >/dev/null 2>&1 || true
        rm -f "$CONFIG_HOME/sonusdeck/graph.pid"
    fi
    rm -f "$BIN_DIR/sonusdeck" "$BIN_DIR/sonusdeck-toggle"
    rm -f "$CONFIG_HOME/autostart/sonusdeck.desktop"
    rm -f "$CONFIG_HOME/systemd/user/sonusdeck.service"
    rm -f "$DATA_HOME/applications/sonusdeck.desktop" \
          "$DATA_HOME/applications/sonusdeck-toggle.desktop"
    rm -rf "$APP_DIR" "$VENV_DIR"
    rmdir "$APP_HOME" 2>/dev/null || true
    log "Done. Settings kept in $CONFIG_HOME/sonusdeck (delete manually if unwanted)."
    exit 0
}

[ "${1:-}" = "uninstall" ] && uninstall

# ------------------------------------------------------------- dependencies

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
        warn "no sudo available; skipping system packages (will try a virtualenv for PyQt6)"
    elif sudo -n true 2>/dev/null || (exec </dev/tty) 2>/dev/null; then
        SUDO="sudo"
    else
        warn "sudo needs a password but no terminal is attached; skipping system packages"
    fi
fi

pkg_install() {
    # Best-effort: missing package names on one distro shouldn't kill the run.
    [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ] || return 0
    if command -v pacman >/dev/null 2>&1; then
        $SUDO pacman -S --needed --noconfirm "$@" || return 1
    elif command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update -qq
        $SUDO apt-get install -y "$@" || return 1
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y "$@" || return 1
    elif command -v zypper >/dev/null 2>&1; then
        $SUDO zypper --non-interactive install "$@" || return 1
    else
        warn "unknown package manager; install python3, PyQt6 and pipewire tools yourself"
        return 1
    fi
}

install_deps() {
    log "Installing system dependencies"
    # SonusDeck drives the graph with pw-dump/pw-cli, pactl and wpctl, so the
    # pulse shim and WirePlumber utilities are needed alongside PipeWire.
    if command -v pacman >/dev/null 2>&1; then
        pkg_install git python python-pyqt6 pipewire libpulse wireplumber || true
        pkg_install easyeffects || warn "could not install EasyEffects (optional)"
    elif command -v apt-get >/dev/null 2>&1; then
        pkg_install git python3 python3-venv python3-pyqt6 \
            pipewire pipewire-pulse pulseaudio-utils wireplumber || true
        pkg_install easyeffects || warn "could not install EasyEffects (optional)"
    elif command -v dnf >/dev/null 2>&1; then
        pkg_install git python3 python3-pyqt6 \
            pipewire pipewire-utils pipewire-pulseaudio pulseaudio-utils wireplumber || true
        pkg_install easyeffects || warn "could not install EasyEffects (optional)"
    elif command -v zypper >/dev/null 2>&1; then
        pkg_install git python3 python3-PyQt6 \
            pipewire pipewire-tools pipewire-pulseaudio wireplumber || true
        pkg_install easyeffects || warn "could not install EasyEffects (optional)"
    fi
}

install_deps

command -v git     >/dev/null 2>&1 || die "git is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v pw-dump >/dev/null 2>&1 || warn "pw-dump not found: SonusDeck needs PipeWire to do anything useful"
command -v pactl   >/dev/null 2>&1 || warn "pactl not found: install your distro's pipewire-pulse / pulseaudio-utils package"
command -v wpctl   >/dev/null 2>&1 || warn "wpctl not found: install wireplumber for volume control"

# ------------------------------------------------------------ fetch the app

# When this script runs from inside a checkout (not piped via curl), install
# that working tree — handy for development and for unpublished forks.
LOCAL_SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$src_dir/sonus_deck.py" ] && [ "$src_dir" != "$APP_DIR" ]; then
        LOCAL_SRC="$src_dir"
    fi
fi

if [ -n "$LOCAL_SRC" ]; then
    log "Installing from local checkout $LOCAL_SRC"
    mkdir -p "$APP_DIR"
    tar -C "$LOCAL_SRC" \
        --exclude .git --exclude __pycache__ --exclude '*.pyc' \
        --exclude _shots --exclude .gitignore \
        -cf - . | tar -C "$APP_DIR" -xf -
elif [ -d "$APP_DIR/.git" ]; then
    log "Updating existing install in $APP_DIR"
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"
else
    log "Cloning $REPO_URL"
    mkdir -p "$APP_HOME"
    rm -rf "$APP_DIR"
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

# ----------------------------------------------------------- pick a python

PY="$(command -v python3)"
if ! "$PY" -c 'import PyQt6' >/dev/null 2>&1; then
    log "Distro PyQt6 not found, using a virtualenv"
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        "$PY" -m venv --system-site-packages "$VENV_DIR" \
            || die "could not create a virtualenv (on Debian/Ubuntu: apt install python3-venv)"
    fi
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip 'PyQt6>=6.5' \
        || die "pip could not install PyQt6"
    PY="$VENV_DIR/bin/python"
fi
"$PY" -c 'import PyQt6' >/dev/null 2>&1 || die "PyQt6 still not importable with $PY"

# -------------------------------------------------------------- launchers

log "Installing launchers into $BIN_DIR"
mkdir -p "$BIN_DIR"
printf '#!/bin/sh\nexec %q %q "$@"\n'          "$PY" "$APP_DIR/sonus_deck.py" > "$BIN_DIR/sonusdeck"
printf '#!/bin/sh\nexec %q %q --toggle "$@"\n' "$PY" "$APP_DIR/sonus_deck.py" > "$BIN_DIR/sonusdeck-toggle"
chmod 755 "$BIN_DIR/sonusdeck" "$BIN_DIR/sonusdeck-toggle"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH; add it to run 'sonusdeck' from a shell" ;;
esac

# ------------------------------------------------------- channels + EasyEffects

log "Stopping a running panel, if any"
"$BIN_DIR/sonusdeck" --quit >/dev/null 2>&1 || true
sleep 0.4

log "Setting up virtual channels"
if "$PY" "$APP_DIR/sonus_deck.py" --setup; then
    log "Game, Chat, Media and Aux are ready"
else
    warn "channel setup had a problem; the panel will retry on launch"
fi

# ------------------------------------------------------------------ launch

if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    log "Starting SonusDeck"
    # First run registers the global shortcut, desktop entry and autostart.
    (setsid "$BIN_DIR/sonusdeck" >/dev/null 2>&1 </dev/null &) || true
    log "Done. Press Ctrl+Alt+V to show or hide the mixer."
else
    log "Done. No display detected: run 'sonusdeck' from your desktop session."
fi
