"""EasyEffects integration.

EasyEffects is the per-device equaliser: it keeps a separate preset for each
output and input device and applies it automatically when that device becomes
active. SonusDeck does not reimplement any of that, it just reports whether the
engine is up and gives you a button to open it.
"""

from __future__ import annotations

import shutil
import subprocess

FLATPAK_ID = "com.github.wwmm.easyeffects"
_TIMEOUT = 2.0


def _native() -> str | None:
    return shutil.which("easyeffects")


def _flatpak_installed() -> bool:
    if shutil.which("flatpak") is None:
        return False
    try:
        out = subprocess.run(
            ["flatpak", "info", FLATPAK_ID],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def installed() -> bool:
    return _native() is not None or _flatpak_installed()


def running() -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-x", "easyeffects"],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def launch() -> bool:
    """Open the EasyEffects window, starting it if needed."""
    native = _native()
    command = [native] if native else (
        ["flatpak", "run", FLATPAK_ID] if _flatpak_installed() else None
    )
    if command is None:
        return False
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


def status_text() -> str:
    if not installed():
        return "EasyEffects not installed"
    if not running():
        return "EasyEffects stopped"
    return ""
