# SonusDeck

An always-on-top **PipeWire** mixer for Linux. One hotkey, six channels plus every app's volume, then straight back to the game.

![Linux](https://img.shields.io/badge/Linux-PipeWire-111?style=flat-square)
![Hotkey](https://img.shields.io/badge/default%20hotkey-Ctrl%2BAlt%2BV-FF5A1F?style=flat-square)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-4F5FD7?style=flat-square)

The panel is a Linux counterpart to [SonarDeck](https://github.com/T3lluz/SonarDeck). Same layout, same faders, same shortcut.

Equalisation is **not** in this overlay. That lives in **EasyEffects**, which already has a parametric EQ, compressor, and a separate preset for each output and input device.

## What you get

| Piece | Role |
| --- | --- |
| **SonusDeck** | Hotkey volume panel. Master, Game, Chat, Media, Aux, Mic, plus per-app faders. |
| **PipeWire loopbacks** | Virtual sinks named `sonusdeck_game`, `_chat`, `_media`, `_aux`. Apps can be sent to a channel. |
| **EasyEffects** | The equaliser. Output EQ for the current playback device, input EQ for the mic. Autoload a different preset per device. |

Audio path:

```
Game / Chat / Media / Aux  →  EasyEffects sink  →  headphones / speakers
Mic                        ←  EasyEffects source ←  microphone
```

If EasyEffects is not running, the channels mix straight into the current hardware sink.

## Install

Needs PipeWire, `wpctl`, `pactl`, and PyQt6.

```bash
# Arch / CachyOS
sudo pacman -S python-pyqt6 easyeffects

git clone git@github.com:T3lluz/SonusDeck.git
cd SonusDeck
python sonus_deck.py
```

Press **Ctrl+Alt+V**. The first launch registers that shortcut with KDE.

EasyEffects is optional but recommended: open it, enable processing on the current output and input, and save an Autoload preset per device (headset, speakers, Bluetooth, …).

## Use

| Action | Result |
| --- | --- |
| `Ctrl+Alt+V` | Show / hide the mixer |
| Drag a fader | Set the level |
| Wheel on a fader | Nudge the level |
| Speaker button | Mute the channel |
| Right-click an app | Send it to Game, Chat, Media, or Aux |
| Wheel on the app row | Scroll through apps |
| Drag the header | Move the panel |
| `Esc` | Close settings, then hide |
| **Open EasyEffects** | Jump to the equaliser |

## Settings

- **Start with session** — launches hidden at login
- **Snap mouse** — cursor jumps to the panel when it opens
- **Virtual channels** — create the Game / Chat / Media / Aux devices
- **Toggle shortcut** — click the chip, then press a new combo (Ctrl, Alt, or Meta required)

Stored in `~/.config/sonusdeck/settings.json`. App-to-channel assignments persist across restarts.

## How it works

SonusDeck starts a small `pipewire -c` process that publishes four loopback sinks. Their outputs are kept on `easyeffects_sink` when EasyEffects is running, so whatever EQ you set there applies to every channel and follows the device EasyEffects is targeting.

Master is the mix target (EasyEffects, or the hardware sink). Mic is `easyeffects_source` when present, otherwise the default capture device.

Nothing is injected into games. Per-app volume uses the same PipeWire session volumes as `pavucontrol`.

## Build from source

```bash
python sonus_deck.py                 # run
python sonus_deck.py --autostart     # start hidden
python sonus_deck.py --toggle        # show / hide the running panel
python sonus_deck.py --install-shortcut
```

`sonus_deck.py` is a launcher; the code lives in the `sonusdeck` package.

| Module | Contents |
| --- | --- |
| `config.py` | App identity, channel list, settings, autostart |
| `theme.py` | Colours and metrics, carried over from SonarDeck |
| `graph.py` | Virtual Game / Chat / Media / Aux sinks |
| `pipewire.py` | Volumes, mute, app sessions, routing |
| `effects.py` | EasyEffects discovery and launch |
| `shortcut.py` | KDE global shortcut |
| `ipc.py` | Single instance and the toggle channel |
| `ui/` | Strip, app mixer, overlay panel |
| `app.py` | Entry point |

## License

MIT. EasyEffects is a separate project (GPL-3.0) and is not bundled.
