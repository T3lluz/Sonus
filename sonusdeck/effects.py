"""EasyEffects integration.

Three jobs:

* Report whether the engine is installed/up and open its window. When it runs,
  the category mix lands in easyeffects_sink so extra device-wide effects
  (compressor, limiter, …) can still apply after the per-category EQs.
* Keep EasyEffects from stacking a second equaliser on that mix. Category EQ
  lives in the PipeWire filter-chain; loading a SonusDeck curve as the
  EasyEffects output preset would colour every stream twice.
* Keep EasyEffects from stealing streams. With "process all output streams"
  on, EasyEffects 8 re-targets every output stream to easyeffects_sink on
  every PipeWire node event, which silently undoes any assignment to a
  SonusDeck category (its file-based exclude list is not reliably honoured
  for streams that appear after startup). ensure_manual_routing() turns that
  setting off once; SonusDeck then owns stream routing itself and still parks
  unassigned apps on easyeffects_sink so post-mix effects keep applying.
  EasyEffects reads its config at startup only, so the one-time flip needs a
  graceful restart (quit via its own CLI, which saves state, then relaunch
  hidden) when it is running — after that, assignments never restart it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import SINK_CHANNELS

FLATPAK_ID = "com.github.wwmm.easyeffects"
_TIMEOUT = 2.0


def _native() -> str | None:
    return shutil.which("easyeffects")


def _command() -> list[str] | None:
    """How to invoke the EasyEffects CLI on this system."""
    native = _native()
    if native:
        return [native]
    if _flatpak_installed():
        return ["flatpak", "run", FLATPAK_ID]
    return None


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


def launch(hidden: bool = False) -> bool:
    """Open the EasyEffects window (or start it hidden), starting it if needed."""
    command = _command()
    if command is None:
        return False
    try:
        subprocess.Popen(
            command + (["-w"] if hidden else []),
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


# ----- keep EasyEffects from stacking a second EQ ----------------------


MIX_PRESET = "SonusDeck Mix"


def _config_home() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME")
    return Path(value) if value else Path.home() / ".config"


def _data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME")
    return Path(value) if value else Path.home() / ".local/share"


def _ee_roots() -> list[Path]:
    """EasyEffects config + data roots (native, and Flatpak if present).

    Presets live under XDG_DATA_HOME/easyeffects/output on EasyEffects 7+;
    older builds used XDG_CONFIG_HOME/easyeffects/output. We touch both.
    """
    roots = [_config_home() / "easyeffects"]
    if _config_home() != Path.home() / ".config":
        # Tests/sandboxes redirect XDG_CONFIG_HOME; stay inside that tree.
        return roots
    roots.append(_data_home() / "easyeffects")
    flatpak_base = Path.home() / ".var" / "app" / FLATPAK_ID
    if flatpak_base.exists():
        roots.append(flatpak_base / "config" / "easyeffects")
        roots.append(flatpak_base / "data" / "easyeffects")
    return roots


def preset_dirs() -> list[Path]:
    return [root / "output" for root in _ee_roots()]


def rc_paths() -> list[Path]:
    """easyeffectsrc locations for the native and (if present) flatpak installs."""
    return [root / "db" / "easyeffectsrc" for root in _ee_roots()]


def _equalizer_db_paths() -> list[Path]:
    return [root / "db" / "equalizerrc" for root in _ee_roots()]


def category_preset_names() -> tuple[str, ...]:
    return tuple(f"SonusDeck {channel.label.title()}" for channel in SINK_CHANNELS)


def _mix_payload() -> dict:
    return {"output": {"blocklist": [], "plugins_order": []}}


def _write_json(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return False
    return True


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _bypass_equalizers(payload: dict) -> bool:
    """Bypass every equalizer plugin in an output preset. Returns True if changed."""
    output = payload.get("output")
    if not isinstance(output, dict):
        return False
    changed = False
    for key, plugin in list(output.items()):
        if not str(key).startswith("equalizer") or not isinstance(plugin, dict):
            continue
        if plugin.get("bypass") is True:
            continue
        plugin["bypass"] = True
        changed = True
    return changed


def _ensure_mix_preset(directory: Path) -> bool:
    """Create or flatten Mix. True when the file was written."""
    path = directory / f"{MIX_PRESET}.json"
    existing = _read_json(path) if path.exists() else None
    if existing is None:
        return _write_json(path, _mix_payload())
    if _bypass_equalizers(existing):
        return _write_json(path, existing)
    return False


def _delete_category_presets(directory: Path) -> None:
    for name in category_preset_names():
        try:
            (directory / f"{name}.json").unlink(missing_ok=True)
        except OSError:
            continue


def _rc_get(text: str, group: str, key: str) -> str:
    header = f"[{group}]"
    prefix = f"{key}="
    in_group = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_group = stripped == header
        elif in_group and stripped.startswith(prefix):
            return stripped[len(prefix):]
    return ""


def _write_rc_key(path: Path, group: str, key: str, value: str) -> bool:
    """Rewrite one KConfig key, preserving the rest of the file.

    Creates the file when missing so a first EasyEffects launch already
    starts with the wanted value.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"[{group}]\n{key}={value}\n", encoding="utf-8")
        except OSError:
            return False
        return True
    except OSError:
        return False
    header = f"[{group}]"
    new_line = f"{key}={value}"
    out: list[str] = []
    in_group = False
    replaced = False
    group_end = -1
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if in_group and not replaced:
                group_end = len(out)
            in_group = stripped == header
        elif in_group and stripped.startswith(f"{key}="):
            out.append(new_line)
            replaced = True
            continue
        out.append(line)
    if not replaced:
        if group_end >= 0:
            while group_end > 0 and not out[group_end - 1].strip():
                group_end -= 1
            out.insert(group_end, new_line)
        elif in_group:
            out.append(new_line)
        else:
            out.extend(["", header, new_line])
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return False
    return True


def _flatten_equalizer_db() -> None:
    """Zero leftover EasyEffects EQ gains so a later plugin add starts flat."""
    pattern = re.compile(r"(band\d+Gain)=[-0-9.]+")
    for path in _equalizer_db_paths():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        updated = pattern.sub(r"\1=0", text)
        if updated == text:
            continue
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(updated, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            continue


def _load_preset(name: str) -> bool:
    command = _command()
    if command is None:
        return False
    try:
        proc = subprocess.run(
            command + ["-l", name],
            capture_output=True, text=True, timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def ensure_passthrough() -> bool:
    """Stop EasyEffects applying a second EQ on the category mix.

    Deletes the old per-category output presets (they were being loaded as
    the device-wide chain), keeps a passthrough "SonusDeck Mix" preset, and
    bypasses any equalizer still in the active output preset. Returns True
    when a running EasyEffects was told to reload.
    """
    names = set(category_preset_names())
    mix_changed = False
    removed_category = False
    for directory in preset_dirs():
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if any((directory / f"{name}.json").exists() for name in names):
            removed_category = True
        _delete_category_presets(directory)
        mix_changed = _ensure_mix_preset(directory) or mix_changed

    _flatten_equalizer_db()

    last = ""
    for path in rc_paths():
        if not path.exists():
            continue
        try:
            last = _rc_get(path.read_text(encoding="utf-8"), "Presets", "lastLoadedOutputPreset")
        except OSError:
            continue
        if last:
            break

    load_name = MIX_PRESET
    need_reload = False
    if last in names or not last:
        load_name = MIX_PRESET
        need_reload = last in names
        for path in rc_paths():
            if path.exists():
                _write_rc_key(path, "Presets", "lastLoadedOutputPreset", MIX_PRESET)
    elif last == MIX_PRESET:
        need_reload = mix_changed or removed_category
    else:
        load_name = last
        for directory in preset_dirs():
            preset_path = directory / f"{last}.json"
            payload = _read_json(preset_path)
            if payload is not None and _bypass_equalizers(payload):
                _write_json(preset_path, payload)
                need_reload = True

    if not need_reload or not running():
        return False
    return _load_preset(load_name)


# ----- keep EasyEffects from stealing routed streams ---------------------


_PIPELINES_GROUP = "EffectsPipelines"
_PROCESS_ALL_KEY = "processAllOutputs"


def _wait_stopped(timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not running():
            return True
        time.sleep(0.15)
    return False


def _quit_gracefully() -> None:
    """Ask EasyEffects to quit through its own CLI so it saves its state."""
    command = _command()
    if command is None:
        return
    try:
        subprocess.run(command + ["-q"], capture_output=True, timeout=10.0)
    except (OSError, subprocess.SubprocessError):
        pass
    _wait_stopped()


def process_all_disabled() -> bool:
    """True when every existing EasyEffects config already has the grab off."""
    found = False
    for path in rc_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found = True
        if _rc_get(text, _PIPELINES_GROUP, _PROCESS_ALL_KEY) != "false":
            return False
    return found


def ensure_manual_routing() -> bool:
    """Turn off "process all output streams" so assignments stick.

    SonusDeck routes every app stream itself (assigned apps to their category
    sink, the rest to easyeffects_sink), so EasyEffects must not re-grab them.
    EasyEffects reads its config at startup only and rewrites it on exit, so
    when the value has to change while it runs: quit (it saves state), write
    the key, relaunch hidden. One-time migration; returns True on restart.
    """
    if not installed():
        return False
    if process_all_disabled():
        return False

    was_running = running()
    if was_running:
        _quit_gracefully()

    # Update every existing config; create only the primary one so a first
    # EasyEffects launch already starts with the grab disabled.
    targets = [path for path in rc_paths() if path.exists()]
    primary = rc_paths()[0]
    if primary not in targets:
        targets.append(primary)
    for path in targets:
        _write_rc_key(path, _PIPELINES_GROUP, _PROCESS_ALL_KEY, "false")

    if was_running:
        launch(hidden=True)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if running():
                break
            time.sleep(0.15)
    return was_running
