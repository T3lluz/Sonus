"""Per-category equaliser model.

Each Sonar-style category (Game, Chat, Media, Aux) is a PipeWire filter-chain
sink with a preamp and ten peaking biquads. This module owns the band layout,
the presets, and (de)serialisation to settings.json. Applying the values to
the live graph happens in pipewire.set_channel_eq.

EasyEffects is *not* a second copy of this EQ. It stays a post-mix slot for
other device-wide effects; stacking another 10-band there is what made the
curve feel aggressive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Ten octave bands, the same layout SteelSeries Sonar uses.
BAND_FREQS: tuple[float, ...] = (
    32.0, 64.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0,
)
BAND_LABELS: tuple[str, ...] = (
    "32", "64", "125", "250", "500", "1K", "2K", "4K", "8K", "16K",
)
BAND_COUNT = len(BAND_FREQS)
# Octave-spaced bells: Q=1.4–1.5 stacks neighbouring boosts too hard, 2.0
# feels thin. 1.8 gives enough overlap that a boost is clearly audible while
# keeping a graphic-EQ feel.
BAND_Q = 1.8
GAIN_LIMIT = 15.0  # dB, either direction
PREAMP_LIMIT = 15.0


def band_name(index: int) -> str:
    """Control-node name inside the filter chain ("band0" .. "band9")."""
    return f"band{index}"


PREAMP_NAME = "preamp"


def clamp_gain(value: float, limit: float = GAIN_LIMIT) -> float:
    return max(-limit, min(limit, float(value)))


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


@dataclass
class ChannelEq:
    enabled: bool = True
    preamp: float = 0.0
    gains: list[float] = field(default_factory=lambda: [0.0] * BAND_COUNT)

    def normalized(self) -> "ChannelEq":
        gains = [clamp_gain(g) for g in (list(self.gains) + [0.0] * BAND_COUNT)[:BAND_COUNT]]
        return ChannelEq(bool(self.enabled), clamp_gain(self.preamp, PREAMP_LIMIT), gains)

    def is_flat(self) -> bool:
        return abs(self.preamp) < 0.05 and all(abs(g) < 0.05 for g in self.gains)

    def signature(self) -> tuple:
        """Hashable identity used to detect when a re-apply is needed."""
        return (self.enabled, round(self.preamp, 2), tuple(round(g, 2) for g in self.gains))

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "preamp": round(self.preamp, 2),
            "gains": [round(g, 2) for g in self.gains],
        }

    @classmethod
    def from_dict(cls, data: object) -> "ChannelEq":
        if not isinstance(data, dict):
            return cls()
        gains = data.get("gains")
        if not isinstance(gains, list):
            gains = []
        try:
            parsed = [float(g) for g in gains]
        except (TypeError, ValueError):
            parsed = []
        try:
            preamp = float(data.get("preamp", 0.0))
        except (TypeError, ValueError):
            preamp = 0.0
        return cls(
            enabled=bool(data.get("enabled", True)),
            preamp=preamp,
            gains=parsed,
        ).normalized()


# Gain curves in band order 32..16K. Assertive enough to hear, but still
# short of a typical "gamer EQ" so a single category doesn't clip or fight a
# device-wide chain. Applying a preset also pulls the preamp down by the peak
# boost (see preset_preamp).
PRESETS: dict[str, tuple[float, ...]] = {
    "Flat": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Bass": (5, 4, 2.5, 1, 0, 0, 0, 0, 0, 0),
    "Voice": (-2, -1.5, 0, 0.5, 2, 3, 3, 2.5, 1, 0),
    "Treble": (0, 0, 0, 0, 0, 0.5, 2, 3, 4, 4.5),
    "Steps": (-3, -2.5, -0.5, 0, 0.5, 2.5, 3.5, 3.5, 2, 0.5),
}


def preset_preamp(gains: tuple[float, ...] | list[float]) -> float:
    """Negative make-up gain so a boosty preset doesn't clip the mix."""
    peak = max((float(g) for g in gains), default=0.0)
    return clamp_gain(-max(0.0, peak), PREAMP_LIMIT)


def load_all(settings: dict, keys: tuple[str, ...]) -> dict[str, ChannelEq]:
    stored = settings.get("eq")
    if not isinstance(stored, dict):
        stored = {}
    return {key: ChannelEq.from_dict(stored.get(key)) for key in keys}


def store_all(settings: dict, eqs: dict[str, ChannelEq]) -> None:
    settings["eq"] = {key: eq.to_dict() for key, eq in eqs.items()}
