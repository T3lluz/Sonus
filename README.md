# SonusDeck

Hotkey volume mixer for PipeWire. Linux counterpart to [SonarDeck](https://github.com/T3lluz/SonarDeck): Master, Game, Chat, Media, Aux, Mic, plus per-app faders.

EQ is **not** in this panel. Use **EasyEffects** (per-device Autoload presets). Channels mix into `easyeffects_sink` when it is running.

## Run

```bash
sudo pacman -S python-pyqt6 easyeffects   # Arch / CachyOS
python sonus_deck.py
```

`Ctrl+Alt+V` shows or hides the mixer (KWin grabs the key on first launch).

## Use

| | |
| --- | --- |
| Left faders | Category volumes (Game / Chat / Media / Aux) and Master / Mic |
| Right faders | Apps currently playing |
| Click **ASSIGN** on an app | Send it to Game, Chat, Media, or Aux |
| **Open EasyEffects** | Equaliser for the current output and mic |
| Drag header / Esc | Move the panel / hide |

Settings and app-to-channel routes: `~/.config/sonusdeck/settings.json`.

## License

MIT.
