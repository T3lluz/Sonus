#!/usr/bin/env bash
# Sonus installer / uninstaller.
#
# Install (also updates an existing install):
#   curl -fsSL https://gitlab.com/T3lluz/Sonus/-/raw/main/install.sh | bash
# Install from a cloned repo:
#   ./install.sh
# Uninstall:
#   curl -fsSL https://gitlab.com/T3lluz/Sonus/-/raw/main/install.sh | bash -s -- uninstall
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

REPO_URL="${SONUSDECK_REPO:-https://gitlab.com/T3lluz/Sonus.git}"
BRANCH="${SONUSDECK_BRANCH:-main}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
APP_HOME="$DATA_HOME/sonusdeck"
APP_DIR="$APP_HOME/app"
VENV_DIR="$APP_HOME/venv"
BIN_DIR="$HOME/.local/bin"

ACTION="install"
for arg in "$@"; do
    case "$arg" in
        --uninstall|uninstall|--remove|remove) ACTION="uninstall" ;;
        --help|-h)
            echo "Usage: install.sh [uninstall]"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (try --help)" >&2
            exit 1
            ;;
    esac
done

# ---------- visuals ----------
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
    C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
    C_CYAN=$'\033[36m'; C_BLUE=$'\033[34m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""
    C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""; C_BLUE=""
fi

LOG_FILE="$(mktemp /tmp/sonusdeck-install.XXXXXX.log)"
WARNINGS=()

banner() {
    printf '\n'
    printf '%s\n' "${C_BLUE}${C_BOLD}  ░█▀▀░█▀█░█▀█░█░█░█▀▀${C_RESET}"
    printf '%s\n' "${C_BLUE}${C_BOLD}  ░▀▀█░█░█░█░█░█░█░▀▀█${C_RESET}"
    printf '%s\n' "${C_BLUE}${C_BOLD}  ░▀▀▀░▀▀▀░▀░▀░▀▀▀░▀▀▀${C_RESET}"
    printf '%s\n\n' "${C_DIM}  Sonar-style PipeWire mixer for Linux${C_RESET}"
}

section() { printf '\n%s\n' "${C_CYAN}${C_BOLD}── $1 ──${C_RESET}"; }
step()    { printf '  %s %-46s' "${C_DIM}▸${C_RESET}" "$1"; }
ok()      { printf '%s\n' "${C_GREEN}✓${C_RESET}"; }
skipped() { printf '%s\n' "${C_DIM}skipped${C_RESET}"; }
failed()  { printf '%s\n' "${C_RED}✗${C_RESET}"; }
warn() {
    printf '%s\n' "${C_YELLOW}!${C_RESET}"
    WARNINGS+=("$1")
}

die() {
    printf '\n  %s %s\n' "${C_RED}${C_BOLD}error:${C_RESET}" "$1" >&2
    printf '  %s\n\n' "${C_DIM}full log: ${LOG_FILE}${C_RESET}" >&2
    exit 1
}

# Run a command quietly, logging output. Returns the command's exit code.
run() { "$@" >>"$LOG_FILE" 2>&1; }

print_warnings() {
    if [ "${#WARNINGS[@]}" -gt 0 ]; then
        printf '\n%s\n' "${C_YELLOW}${C_BOLD}  Warnings:${C_RESET}"
        for w in "${WARNINGS[@]}"; do
            printf '  %s %s\n' "${C_YELLOW}•${C_RESET}" "$w"
        done
    fi
}

banner

# ---------- uninstall ----------
if [ "$ACTION" = "uninstall" ]; then
    section "Stopping Sonus"

    step "Panel"
    run "$BIN_DIR/sonusdeck" --quit || true
    ok

    step "User service"
    if command -v systemctl >/dev/null 2>&1; then
        run systemctl --user disable --now sonusdeck.service || true
        ok
    else
        skipped
    fi

    step "Channel graph"
    if [ -f "$CONFIG_HOME/sonusdeck/graph.pid" ]; then
        kill "$(cat "$CONFIG_HOME/sonusdeck/graph.pid")" >/dev/null 2>&1 || true
        rm -f "$CONFIG_HOME/sonusdeck/graph.pid"
        ok
    else
        skipped
    fi

    section "Removing files"

    step "Launchers"
    rm -f "$BIN_DIR/sonusdeck" "$BIN_DIR/sonusdeck-toggle"
    ok

    step "Desktop entries, icon + autostart"
    rm -f "$CONFIG_HOME/autostart/sonusdeck.desktop" \
          "$CONFIG_HOME/systemd/user/sonusdeck.service" \
          "$DATA_HOME/applications/sonusdeck.desktop" \
          "$DATA_HOME/applications/sonusdeck-toggle.desktop" \
          "$DATA_HOME/icons/hicolor/512x512/apps/sonusdeck.png"
    ok

    step "Application + virtualenv"
    rm -rf "$APP_DIR" "$VENV_DIR"
    rmdir "$APP_HOME" 2>/dev/null || true
    ok

    printf '\n%s\n' "${C_GREEN}${C_BOLD}  Sonus removed.${C_RESET}"
    print_warnings
    printf '\n  %s\n' "${C_DIM}Settings kept in ${CONFIG_HOME}/sonusdeck (delete manually if unwanted).${C_RESET}"
    printf '  %s\n\n' "${C_DIM}Log: ${LOG_FILE}${C_RESET}"
    exit 0
fi

# ---------- system dependencies ----------
section "System dependencies"

SUDO=""
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif ! command -v sudo >/dev/null 2>&1; then
    step "Package manager access"
    warn "no sudo available — skipping system packages (a virtualenv covers PyQt6)"
elif sudo -n true 2>/dev/null; then
    SUDO="sudo"
elif { true < /dev/tty; } 2>/dev/null; then
    printf '  %s\n' "${C_DIM}Installing packages needs sudo.${C_RESET}"
    if sudo -v < /dev/tty; then
        SUDO="sudo"
    else
        step "Package manager access"
        warn "sudo authentication failed — skipping system packages"
    fi
else
    step "Package manager access"
    warn "sudo needs a password but no terminal is attached — skipping system packages"
fi

pkg_install() {
    # Best-effort: missing package names on one distro shouldn't kill the run.
    [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ] || return 1
    if command -v pacman >/dev/null 2>&1; then
        run $SUDO pacman -S --needed --noconfirm "$@"
    elif command -v apt-get >/dev/null 2>&1; then
        run $SUDO apt-get install -y "$@"
    elif command -v dnf >/dev/null 2>&1; then
        run $SUDO dnf install -y "$@"
    elif command -v zypper >/dev/null 2>&1; then
        run $SUDO zypper --non-interactive install "$@"
    else
        return 1
    fi
}

if [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; then
    # Sonus drives the graph with pw-dump/pw-cli, pactl and wpctl, so the
    # pulse shim and WirePlumber utilities are needed alongside PipeWire.
    BASE_PKGS=""
    if command -v pacman >/dev/null 2>&1; then
        BASE_PKGS="git python python-pyqt6 pipewire libpulse wireplumber"
    elif command -v apt-get >/dev/null 2>&1; then
        step "Package index (apt)"
        if run $SUDO apt-get update -qq; then ok; else warn "apt-get update failed"; fi
        BASE_PKGS="git python3 python3-venv python3-pyqt6 pipewire pipewire-pulse pulseaudio-utils wireplumber"
    elif command -v dnf >/dev/null 2>&1; then
        BASE_PKGS="git python3 python3-pyqt6 pipewire pipewire-utils pipewire-pulseaudio pulseaudio-utils wireplumber"
    elif command -v zypper >/dev/null 2>&1; then
        BASE_PKGS="git python3 python3-PyQt6 pipewire pipewire-tools pipewire-pulseaudio wireplumber"
    fi

    if [ -n "$BASE_PKGS" ]; then
        step "Python, PyQt6 + PipeWire tools"
        # shellcheck disable=SC2086
        if pkg_install $BASE_PKGS; then ok; else
            warn "some base packages failed to install (see log) — continuing"
        fi
        step "EasyEffects (optional)"
        if pkg_install easyeffects; then ok; else
            warn "could not install EasyEffects — post-mix effects stay unavailable"
        fi
    else
        step "Package manager"
        warn "unknown package manager — install python3, PyQt6 and pipewire tools yourself"
    fi
fi

# ---------- tool check ----------
section "Checking tools"

require() {
    step "$1"
    if command -v "$1" >/dev/null 2>&1; then ok; else
        failed
        die "'$1' not found — install it first, then re-run"
    fi
}

recommend() {
    step "$1"
    if command -v "$1" >/dev/null 2>&1; then ok; else
        warn "'$1' not found — $2"
    fi
}

require git
require python3
recommend pw-dump "Sonus needs PipeWire to do anything useful"
recommend pactl   "install your distro's pipewire-pulse / pulseaudio-utils package"
recommend wpctl   "install wireplumber for volume control"

# ---------- fetch the app ----------
section "Fetching Sonus"

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
    step "Local checkout -> $APP_DIR"
    mkdir -p "$APP_DIR"
    tar -C "$LOCAL_SRC" \
        --exclude .git --exclude __pycache__ --exclude '*.pyc' \
        --exclude _shots --exclude docs --exclude .gitignore \
        -cf - . | tar -C "$APP_DIR" -xf -
    ok
elif [ -d "$APP_DIR/.git" ]; then
    step "Updating existing install"
    # Installs made before the move still point at the old host.
    run git -C "$APP_DIR" remote set-url origin "$REPO_URL" || true
    run git -C "$APP_DIR" fetch origin "$BRANCH" || die "git fetch failed"
    run git -C "$APP_DIR" reset --hard "origin/$BRANCH" || die "git reset failed"
    ok
else
    step "Cloning $REPO_URL"
    mkdir -p "$APP_HOME"
    rm -rf "$APP_DIR"
    run git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR" || die "git clone failed"
    ok
fi

# ---------- python runtime ----------
section "Python runtime"

PY="$(command -v python3)"
step "PyQt6 (distro package)"
if "$PY" -c 'import PyQt6' >/dev/null 2>&1; then
    ok
else
    skipped
    step "PyQt6 (private virtualenv)"
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        run "$PY" -m venv --system-site-packages "$VENV_DIR" \
            || die "could not create a virtualenv (on Debian/Ubuntu: apt install python3-venv)"
    fi
    run "$VENV_DIR/bin/pip" install --upgrade pip 'PyQt6>=6.5' \
        || die "pip could not install PyQt6"
    PY="$VENV_DIR/bin/python"
    ok
fi
"$PY" -c 'import PyQt6' >/dev/null 2>&1 || die "PyQt6 still not importable with $PY"

# ---------- launchers ----------
section "Installing launchers"

step "sonusdeck + sonusdeck-toggle -> ~/.local/bin"
mkdir -p "$BIN_DIR"
printf '#!/bin/sh\nexec %q %q "$@"\n'          "$PY" "$APP_DIR/sonus_deck.py" > "$BIN_DIR/sonusdeck"
printf '#!/bin/sh\nexec %q %q --toggle "$@"\n' "$PY" "$APP_DIR/sonus_deck.py" > "$BIN_DIR/sonusdeck-toggle"
chmod 755 "$BIN_DIR/sonusdeck" "$BIN_DIR/sonusdeck-toggle"
ok

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        step "PATH check"
        warn "$BIN_DIR is not on your PATH — add it to run 'sonusdeck' from a shell"
        ;;
esac

# ---------- audio graph ----------
section "Audio graph"

step "Stopping a running panel"
run "$BIN_DIR/sonusdeck" --quit || true
sleep 0.4
ok

step "Game / Chat / Media / Aux sinks"
if run "$PY" "$APP_DIR/sonus_deck.py" --setup; then
    ok
else
    warn "channel setup had a problem — the panel will retry on launch"
fi

# ---------- launch ----------
section "Finishing up"

step "Starting Sonus"
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    # First run registers the global shortcut, desktop entry and start-on-login,
    # then waits in the background — the panel stays hidden until the hotkey.
    (setsid "$BIN_DIR/sonusdeck" --autostart >>"$LOG_FILE" 2>&1 </dev/null &) || true
    ok
else
    warn "no display detected — run 'sonusdeck' from your desktop session"
fi

# ---------- summary ----------
printf '\n%s\n' "${C_GREEN}${C_BOLD}  Sonus installed successfully.${C_RESET}"
print_warnings
cat <<EOF

  ${C_BOLD}Next step:${C_RESET} press ${C_BOLD}Ctrl+Alt+V${C_RESET} to show or hide the mixer

  ${C_DIM}Install log: ${LOG_FILE}${C_RESET}

EOF
