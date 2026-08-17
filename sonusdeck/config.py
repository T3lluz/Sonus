"""App identity, channel definitions, and on-disk settings."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "SonusDeck"
APP_ID = "sonusdeck"
NODE_PREFIX = "sonusdeck"
# Fixed binding; rebind the "SonusDeck Toggle" entry in the desktop's own
# shortcut settings instead of through the panel.
HOTKEY = "Ctrl+Alt+V"


@dataclass(frozen=True)
class Channel:
    key: str
    label: str
    accent: str
    kind: str  # "master" | "sink"

    @property
    def node_name(self) -> str:
        return f"{NODE_PREFIX}_{self.key}"

    @property
    def description(self) -> str:
        return f"{APP_NAME} {self.label.title()}"


CHANNELS: tuple[Channel, ...] = (
    Channel("master", "MASTER", "#4F5EDF", "master"),
    Channel("game", "GAME", "#35B697", "sink"),
    Channel("chat", "CHAT", "#539FEA", "sink"),
    Channel("media", "MEDIA", "#E562AE", "sink"),
    Channel("aux", "AUX", "#8658F9", "sink"),
)

SINK_CHANNELS = tuple(c for c in CHANNELS if c.kind == "sink")
ROUTABLE = SINK_CHANNELS
CHANNEL_BY_KEY = {c.key: c for c in CHANNELS}
CHANNEL_BY_NODE = {c.node_name: c for c in CHANNELS}


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / APP_ID
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / APP_ID
SETTINGS_PATH = CONFIG_DIR / "settings.json"
GRAPH_CONF = "graph.conf"
GRAPH_CONF_PATH = CONFIG_DIR / GRAPH_CONF
AUTOSTART_PATH = _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / f"{APP_ID}.desktop"
SYSTEMD_UNIT_PATH = _xdg("XDG_CONFIG_HOME", ".config") / "systemd" / "user" / f"{APP_ID}.service"
DESKTOP_PATH = _xdg("XDG_DATA_HOME", ".local/share") / "applications" / f"{APP_ID}-toggle.desktop"
LAUNCH_DESKTOP_PATH = _xdg("XDG_DATA_HOME", ".local/share") / "applications" / f"{APP_ID}.desktop"
BIN_DIR = Path.home() / ".local/bin"
TOGGLE_BIN = BIN_DIR / f"{APP_ID}-toggle"
LAUNCH_BIN = BIN_DIR / APP_ID
DBUS_SERVICE = "dev.t3lluz.SonusDeck"
DBUS_PATH = "/Panel"
DBUS_INTERFACE = "dev.t3lluz.SonusDeck"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    return Path(base) if base else Path("/tmp")


SOCKET_PATH = runtime_dir() / f"{APP_ID}.sock"


DEFAULTS: dict = {
    "autostart": True,
    "pos_x": None,
    "pos_y": None,
    "manage_graph": True,
    "routes": {},
    "eq": {},
}


def load_settings() -> dict:
    data = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            data.update(stored)
    except (OSError, json.JSONDecodeError):
        pass
    return data


def save_settings(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    tmp.replace(SETTINGS_PATH)


def _script() -> Path:
    return Path(__file__).resolve().parent.parent / "sonus_deck.py"


def python_executable() -> str:
    """Real interpreter, not the Cursor AppImage that launched us."""
    exe = sys.executable or ""
    low = exe.lower()
    if exe and os.path.isfile(exe) and "appimage" not in low and "cursor" not in Path(exe).name.lower():
        return exe
    return shutil.which("python3") or "/usr/bin/python3"


def write_wrappers() -> None:
    """Install ~/.local/bin launchers. KDE shortcuts need a single unquoted Exec."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    py = python_executable()
    script = str(_script())
    TOGGLE_BIN.write_text(
        f"#!/bin/sh\nexec {shlex.quote(py)} {shlex.quote(script)} --toggle \"$@\"\n",
        encoding="utf-8",
    )
    LAUNCH_BIN.write_text(
        f"#!/bin/sh\nexec {shlex.quote(py)} {shlex.quote(script)} \"$@\"\n",
        encoding="utf-8",
    )
    mode = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    TOGGLE_BIN.chmod(mode)
    LAUNCH_BIN.chmod(mode)


def launch_command() -> str:
    write_wrappers()
    return str(LAUNCH_BIN) + " --autostart"


def toggle_command() -> str:
    write_wrappers()
    return str(TOGGLE_BIN)


def show_command() -> str:
    write_wrappers()
    return str(LAUNCH_BIN)


_DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name={name}
Comment={comment}
Exec={exec}
Icon=audio-volume-high
Terminal=false
Categories=AudioVideo;Audio;Mixer;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-KDE-Shortcuts={shortcuts}
NoDisplay={nodisplay}
"""


_SYSTEMD_UNIT_TEMPLATE = """[Unit]
Description={name} hotkey volume mixer
# Fallback for sessions without XDG autostart. If the desktop already
# launched us, this copy sees the instance socket and exits cleanly.
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart={exec}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""


def _systemctl(*args: str) -> bool:
    if shutil.which("systemctl") is None:
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", *args],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def set_autostart(enabled: bool) -> None:
    """Start with the system: XDG autostart plus a systemd user unit.

    XDG autostart covers every mainstream desktop; the systemd unit picks up
    sessions that activate graphical-session.target but skip XDG autostart.
    Double launches are safe: the second instance exits on the socket guard.
    """
    if not enabled:
        AUTOSTART_PATH.unlink(missing_ok=True)
        if SYSTEMD_UNIT_PATH.exists():
            _systemctl("disable", f"{APP_ID}.service")
            SYSTEMD_UNIT_PATH.unlink(missing_ok=True)
            _systemctl("daemon-reload")
        return
    command = launch_command()
    AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_PATH.write_text(
        _DESKTOP_TEMPLATE.format(
            name=APP_NAME,
            comment="Hotkey volume mixer for PipeWire",
            exec=command,
            nodisplay="true",
            shortcuts="",
        ),
        encoding="utf-8",
    )
    try:
        SYSTEMD_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD_UNIT_PATH.write_text(
            _SYSTEMD_UNIT_TEMPLATE.format(name=APP_NAME, exec=command),
            encoding="utf-8",
        )
        _systemctl("daemon-reload")
        _systemctl("enable", f"{APP_ID}.service")
    except OSError:
        pass


def write_toggle_desktop(hotkey: str = HOTKEY) -> Path:
    DESKTOP_PATH.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_PATH.write_text(
        _DESKTOP_TEMPLATE.format(
            name=f"{APP_NAME} Toggle",
            comment="Show or hide the SonusDeck mixer",
            exec=toggle_command(),
            nodisplay="true",
            shortcuts=hotkey,
        ),
        encoding="utf-8",
    )
    return DESKTOP_PATH


def write_launch_desktop() -> Path:
    LAUNCH_DESKTOP_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_DESKTOP_PATH.write_text(
        _DESKTOP_TEMPLATE.format(
            name=APP_NAME,
            comment="Hotkey volume mixer for PipeWire",
            exec=show_command(),
            nodisplay="false",
            shortcuts="",
        ),
        encoding="utf-8",
    )
    return LAUNCH_DESKTOP_PATH
