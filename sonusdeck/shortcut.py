"""Global shortcut registration for KDE Plasma Wayland.

The compositor owns global keys. We bind Ctrl+Alt+V three ways:

1. A ~/.local/bin wrapper (same pattern as other working KDE service shortcuts)
2. KGlobalAccel ``_launch`` on that desktop file
3. A KWin script that calls Sonus over D-Bus (this is what actually
   reaches the key when a Wayland app has focus)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PyQt6.QtGui import QKeySequence

from .config import APP_NAME, DESKTOP_PATH, write_toggle_desktop, write_wrappers

SHORTCUTS_FILE = "kglobalshortcutsrc"
FRIENDLY = f"{APP_NAME} Toggle"
ACTION_ID = [DESKTOP_PATH.name, APP_NAME, "_launch", "Toggle"]
KWIN_PLUGIN = "sonusdecktoggle"
KWIN_SCRIPT_SRC = Path(__file__).resolve().parent.parent / "kwin" / KWIN_PLUGIN
KWIN_SCRIPT_DST = Path.home() / ".local/share/kwin/scripts" / KWIN_PLUGIN
_TIMEOUT = 5.0


def _run(args: list[str], timeout: float = _TIMEOUT) -> tuple[bool, str]:
    if shutil.which(args[0]) is None:
        return False, f"{args[0]} not found"
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def plasma() -> bool:
    return shutil.which("kwriteconfig6") is not None


def _group() -> str:
    return DESKTOP_PATH.name


def normalise(spec: str) -> str:
    """`ctrl+alt+v` and `Ctrl+Alt+V` are the same binding to KDE."""
    parts = [p.strip() for p in spec.replace("-", "+").split("+") if p.strip()]
    names = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "meta": "Meta", "super": "Meta"}
    out = []
    for part in parts:
        low = part.lower()
        out.append(names.get(low, part.upper() if len(part) == 1 else part.title()))
    return "+".join(out)


def _key_code(spec: str) -> int:
    sequence = QKeySequence(normalise(spec))
    if sequence.count() == 0:
        return 0
    return int(sequence[0].toCombined())


def _gdbus(method: str, *args: str) -> tuple[bool, str]:
    command = [
        "gdbus", "call", "--session",
        "--dest", "org.kde.kglobalaccel",
        "--object-path", "/kglobalaccel",
        "--method", f"org.kde.KGlobalAccel.{method}",
        *args,
    ]
    return _run(command)


def _dbus_list(values: list[str]) -> str:
    inner = ", ".join(f"'{item}'" for item in values)
    return f"[{inner}]"


def _install_kwin_script(key: str) -> tuple[bool, str]:
    src = KWIN_SCRIPT_SRC / "contents" / "code" / "main.js"
    meta = KWIN_SCRIPT_SRC / "metadata.json"
    if not src.is_file() or not meta.is_file():
        return False, "kwin script sources missing"
    dest_js = KWIN_SCRIPT_DST / "contents" / "code" / "main.js"
    dest_js.parent.mkdir(parents=True, exist_ok=True)
    dest_js.write_text(src.read_text(encoding="utf-8").replace("HOTKEY", key), encoding="utf-8")
    (KWIN_SCRIPT_DST / "metadata.json").write_text(meta.read_text(encoding="utf-8"), encoding="utf-8")

    _run([
        "kwriteconfig6", "--file", "kwinrc",
        "--group", "Plugins", "--key", f"{KWIN_PLUGIN}Enabled", "true",
    ])
    if shutil.which("qdbus6") is None:
        return True, "kwin script installed; reconfigure KWin to load it"

    _run(["qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", KWIN_PLUGIN])
    ok, detail = _run([
        "qdbus6", "org.kde.KWin", "/Scripting",
        "org.kde.kwin.Scripting.loadScript", str(dest_js), KWIN_PLUGIN,
    ])
    _run(["qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start"])
    _run(["qdbus6", "org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"])
    if ok:
        return True, f"{key} bound in KWin"
    return False, detail or "could not load the KWin script"


def install(spec: str) -> tuple[bool, str]:
    """Bind `spec` to the toggle entry. Returns (ok, human readable detail)."""
    key = normalise(spec)
    write_wrappers()
    write_toggle_desktop(key)
    code = _key_code(key)
    if not code:
        return False, f"could not parse {spec}"

    if plasma():
        _run([
            "kwriteconfig6", "--file", SHORTCUTS_FILE,
            "--group", "services", "--group", _group(),
            "--key", "_launch", "none",
        ])

    if shutil.which("gdbus"):
        action = _dbus_list(ACTION_ID)
        _gdbus("unregister", DESKTOP_PATH.name, "_launch")
        _gdbus("unRegister", action)

    kwin_ok, kwin_detail = _install_kwin_script(key)
    if kwin_ok:
        return True, kwin_detail
    return True, f"{key} saved; {kwin_detail}"


def remove() -> None:
    if plasma():
        _run([
            "kwriteconfig6", "--file", SHORTCUTS_FILE,
            "--group", "services", "--group", _group(),
            "--key", "_launch", "--delete",
        ])
    if shutil.which("gdbus"):
        _gdbus("unRegister", _dbus_list(ACTION_ID))
    DESKTOP_PATH.unlink(missing_ok=True)


def current() -> str:
    if not shutil.which("kreadconfig6"):
        return ""
    try:
        result = subprocess.run(
            [
                "kreadconfig6", "--file", SHORTCUTS_FILE,
                "--group", "services", "--group", _group(), "--key", "_launch",
            ],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = result.stdout.strip().split(",")[0]
    return value if value and value != "none" else ""
