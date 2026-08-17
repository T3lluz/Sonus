"""Global shortcut registration for KDE Plasma.

Wayland does not let an application grab keys for itself, so the shortcut is
registered with the compositor instead: a hidden desktop entry runs
`sonusdeck --toggle`, and KDE binds the key combination to that entry.
"""

from __future__ import annotations

import shutil
import subprocess

from .config import APP_NAME, DESKTOP_PATH, write_toggle_desktop

SHORTCUTS_FILE = "kglobalshortcutsrc"
FRIENDLY = f"{APP_NAME} Toggle"
_TIMEOUT = 5.0


def _run(args: list[str]) -> tuple[bool, str]:
    if shutil.which(args[0]) is None:
        return False, f"{args[0]} not found"
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT)
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


def install(spec: str) -> tuple[bool, str]:
    """Bind `spec` to the toggle entry. Returns (ok, human readable detail)."""
    write_toggle_desktop()
    if not plasma():
        return False, "kwriteconfig6 not found; bind the shortcut manually"

    key = normalise(spec)
    ok, detail = _run([
        "kwriteconfig6", "--file", SHORTCUTS_FILE,
        "--group", "services", "--group", _group(),
        "--key", "_launch", f"{key},none,{FRIENDLY}",
    ])
    if not ok:
        return False, detail or "could not write kglobalshortcutsrc"

    _run([
        "kwriteconfig6", "--file", SHORTCUTS_FILE,
        "--group", "services", "--group", _group(),
        "--key", "_k_friendly_name", FRIENDLY,
    ])
    reloaded, detail = reload_daemon()
    if reloaded:
        return True, f"{key} registered with KDE"
    return True, f"{key} saved; log out and back in to activate"


def remove() -> None:
    if plasma():
        _run([
            "kwriteconfig6", "--file", SHORTCUTS_FILE,
            "--group", "services", "--group", _group(),
            "--key", "_launch", "--delete",
        ])
    DESKTOP_PATH.unlink(missing_ok=True)
    reload_daemon()


def reload_daemon() -> tuple[bool, str]:
    """Ask kglobalaccel to pick up the edited config."""
    for args in (
        ["systemctl", "--user", "restart", "plasma-kglobalaccel.service"],
        ["systemctl", "--user", "restart", "app-org.kde.kglobalacceld.service"],
    ):
        ok, detail = _run(args)
        if ok:
            return True, detail
    return False, "kglobalaccel not restarted"


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
    return result.stdout.strip().split(",")[0]
