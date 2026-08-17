"""Reading and driving the PipeWire graph through the standard CLI tools."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

from . import eq as eqmod
from .config import NODE_PREFIX, SINK_CHANNELS

STREAM_CLASSES = ("Stream/Output/Audio",)
_TIMEOUT = 2.5
_SKIP_APPS = frozenset({
    "easyeffects",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "sonusdeck",
})

# PipeWire stores volumes on a cubic curve; wpctl and the percentage in the
# panel both speak the linear one.
_CUBE = 1.0 / 3.0

EFFECTS_SINK = "easyeffects_sink"


def _linear(cubic: float) -> float:
    return max(0.0, min(1.0, cubic ** _CUBE)) if cubic > 0 else 0.0


def _run(args: list[str], timeout: float = _TIMEOUT) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


@dataclass
class NodeState:
    node_id: int
    serial: int
    name: str
    description: str
    volume: float = 1.0
    muted: bool = False


@dataclass
class AppStream:
    key: str
    name: str
    node_id: int
    serial: int
    volume: float = 1.0
    muted: bool = False
    icon_name: str = ""
    binary: str = ""
    channel: str = ""
    members: list[int] = field(default_factory=list)
    # Object serial of every member stream (== pactl sink-input index);
    # moving an app means moving each of these.
    serials: list[int] = field(default_factory=list)


@dataclass
class Snapshot:
    channels: dict[str, NodeState] = field(default_factory=dict)
    apps: list[AppStream] = field(default_factory=list)
    output_serials: dict[str, int] = field(default_factory=dict)
    default_sink: str = ""
    mix_target: str = ""
    ready: bool = False


def _props_volume(info: dict) -> tuple[float, bool]:
    params = (info.get("params") or {}).get("Props") or []
    volume, muted = 1.0, False
    for entry in params:
        if "channelVolumes" in entry:
            vols = entry.get("channelVolumes") or []
            if vols:
                volume = _linear(max(float(v) for v in vols))
        elif "volume" in entry and "channelVolumes" not in entry:
            volume = _linear(float(entry.get("volume", 1.0)))
        if "mute" in entry:
            muted = bool(entry.get("mute"))
    return volume, muted


def _app_identity(props: dict) -> tuple[str, str, str, str]:
    binary = str(props.get("application.process.binary") or "")
    app_name = str(props.get("application.name") or "")
    media = str(props.get("media.name") or "")
    icon = str(props.get("application.icon-name") or props.get("application.icon") or "")
    label = app_name or binary or media or "App"
    key = (binary or app_name or label).lower()
    if not icon:
        icon = (binary or app_name).lower()
    return key, label, icon, binary


def _sink_input_map() -> dict[int, int]:
    """Stream pulse index -> sink pulse index (both are PipeWire object serials)."""
    mapping: dict[int, int] = {}
    for line in _run(["pactl", "list", "short", "sink-inputs"]).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                mapping[int(parts[0])] = int(parts[1])
            except ValueError:
                continue
    return mapping


def _short_names(kind: str) -> list[str]:
    names: list[str] = []
    for line in _run(["pactl", "list", "short", kind]).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            names.append(parts[1])
    return names


def default_sink_name() -> str:
    return _run(["pactl", "get-default-sink"]).strip()


def mix_target_name() -> str:
    """Where channel outputs should land so EasyEffects can EQ the device."""
    sinks = _short_names("sinks")
    if EFFECTS_SINK in sinks:
        return EFFECTS_SINK
    default = default_sink_name()
    if default and not default.startswith(NODE_PREFIX):
        return default
    for name in sinks:
        if not name.startswith(NODE_PREFIX):
            return name
    return default


def snapshot() -> Snapshot:
    raw = _run(["pw-dump"], timeout=4.0)
    snap = Snapshot(default_sink=default_sink_name(), mix_target=mix_target_name())
    if not raw:
        return snap
    try:
        objects = json.loads(raw)
    except json.JSONDecodeError:
        return snap

    nodes: list[tuple[dict, dict, dict]] = []
    by_name: dict[str, tuple[dict, dict, dict]] = {}
    for obj in objects:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        nodes.append((obj, info, props))
        name = str(props.get("node.name") or "")
        if name:
            by_name[name] = (obj, info, props)

    serial_to_channel: dict[int, str] = {}
    output_by_name = {f"output.{channel.node_name}": channel.key for channel in SINK_CHANNELS}

    for obj, info, props in nodes:
        name = str(props.get("node.name") or "")
        key = output_by_name.get(name)
        if key:
            serial = int(props.get("object.serial") or 0)
            if serial:
                snap.output_serials[key] = serial

    for channel in SINK_CHANNELS:
        found = by_name.get(channel.node_name)
        if found is None:
            continue
        obj, info, props = found
        volume, muted = _props_volume(info)
        serial = int(props.get("object.serial") or 0)
        snap.channels[channel.key] = NodeState(
            node_id=int(obj["id"]),
            serial=serial,
            name=channel.node_name,
            description=str(props.get("node.description") or channel.description),
            volume=volume,
            muted=muted,
        )
        serial_to_channel[serial] = channel.key

    master = by_name.get(snap.mix_target) or by_name.get(snap.default_sink)
    if master is not None:
        obj, info, props = master
        volume, muted = _props_volume(info)
        snap.channels["master"] = NodeState(
            node_id=int(obj["id"]),
            serial=int(props.get("object.serial") or 0),
            name=str(props.get("node.name") or ""),
            description=str(props.get("node.description") or "Output"),
            volume=volume,
            muted=muted,
        )

    routes = _sink_input_map()
    grouped: dict[str, AppStream] = {}
    for obj, info, props in nodes:
        if str(props.get("media.class") or "") not in STREAM_CLASSES:
            continue
        name = str(props.get("node.name") or "")
        if name.startswith(f"output.{NODE_PREFIX}_") or name.startswith(NODE_PREFIX):
            continue
        key, label, icon, binary = _app_identity(props)
        if key in _SKIP_APPS or binary.lower() in _SKIP_APPS:
            continue
        serial = int(props.get("object.serial") or 0)
        volume, muted = _props_volume(info)
        existing = grouped.get(key)
        if existing is not None:
            existing.members.append(int(obj["id"]))
            if serial:
                existing.serials.append(serial)
            continue
        grouped[key] = AppStream(
            key=key,
            name=label,
            node_id=int(obj["id"]),
            serial=serial,
            volume=volume,
            muted=muted,
            icon_name=icon,
            binary=binary,
            channel=serial_to_channel.get(routes.get(serial, -1), ""),
            members=[int(obj["id"])],
            serials=[serial] if serial else [],
        )

    snap.apps = sorted(grouped.values(), key=lambda a: a.name.lower())
    snap.ready = any(key in snap.channels for key in ("game", "chat", "media", "aux"))
    return snap


def set_volume(node_id: int, volume: float) -> None:
    value = max(0.0, min(1.0, float(volume)))
    _run(["wpctl", "set-volume", str(node_id), f"{value:.4f}"])


def set_mute(node_id: int, muted: bool) -> None:
    _run(["wpctl", "set-mute", str(node_id), "1" if muted else "0"])


def move_stream(serial: int, sink_name: str) -> None:
    _run(["pactl", "move-sink-input", str(serial), sink_name])


def _eq_payload(state: eqmod.ChannelEq) -> str:
    """Props "params" list; a disabled EQ is written as an effective bypass."""
    state = state.normalized()
    active = state.enabled
    mult = eqmod.db_to_linear(state.preamp) if active else 1.0
    parts = [f'"{eqmod.PREAMP_NAME}:Mult" {mult:.6f}']
    for index in range(eqmod.BAND_COUNT):
        gain = state.gains[index] if active else 0.0
        freq = eqmod.BAND_FREQS[index]
        parts.append(
            f'"{eqmod.band_name(index)}:Freq" {freq:.1f} '
            f'"{eqmod.band_name(index)}:Q" {eqmod.BAND_Q:.2f} '
            f'"{eqmod.band_name(index)}:Gain" {gain:.2f}'
        )
    return "{ params = [ " + " ".join(parts) + " ] }"


def set_channel_eq(node_id: int, state: eqmod.ChannelEq) -> bool:
    """Push preamp and band gains into a channel's filter-chain sink node.

    The Props "params" values reach the DSP even though pw-cli's readback of
    that param can show stale values — verified with a signal-level test.
    """
    out = _run(["pw-cli", "set-param", str(node_id), "Props", _eq_payload(state)])
    return bool(out)


def _sink_names_by_index() -> dict[int, str]:
    """Sink pulse index (== object serial) -> node name."""
    sinks: dict[int, str] = {}
    for line in _run(["pactl", "list", "short", "sinks"]).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                sinks[int(parts[0])] = parts[1]
            except ValueError:
                continue
    return sinks


def retarget_channel_outputs(snap: Snapshot) -> None:
    """Keep Game/Chat/Media/Aux feeding EasyEffects (or the hardware sink)."""
    target = snap.mix_target
    if not target or not snap.output_serials:
        return
    routes = _sink_input_map()
    sink_names = _sink_names_by_index()
    for serial in snap.output_serials.values():
        current = sink_names.get(routes.get(serial, -1), "")
        if current != target:
            move_stream(serial, target)


def enforce_app_routes(snap: Snapshot, routes: dict[str, str]) -> None:
    """Keep every app stream where Sonus routed it.

    Assigned apps play into their category sink; everything else is parked on
    the mix target (easyeffects_sink while EasyEffects runs) so post-mix
    effects still apply. Runs on every poll, so nothing — an EasyEffects
    started later, session restores — can silently steal a stream.
    """
    if not snap.apps:
        return
    channel_sinks = {
        channel.key: channel.node_name
        for channel in SINK_CHANNELS
        if channel.key in snap.channels
    }
    stream_sinks = _sink_input_map()
    sink_names = _sink_names_by_index()
    grabbing = snap.mix_target == EFFECTS_SINK
    for app in snap.apps:
        wanted = channel_sinks.get(routes.get(app.binary or app.key, ""))
        for serial in app.serials or [app.serial]:
            if not serial:
                continue
            current = sink_names.get(stream_sinks.get(serial, -1), "")
            if wanted:
                target = wanted
            elif current.startswith(NODE_PREFIX) or (grabbing and current != EFFECTS_SINK):
                # Unassigned: never leave a stream on a category sink, and
                # feed it to EasyEffects when that is the mix target.
                target = snap.mix_target
            else:
                continue
            if current and current != target:
                move_stream(serial, target)


def channels_present() -> bool:
    sinks = _run(["pactl", "list", "short", "sinks"])
    return all(channel.node_name in sinks for channel in SINK_CHANNELS)
