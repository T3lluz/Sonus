# SonusDeck

Hotkey volume mixer for PipeWire. Linux counterpart to [SteelSeries Sonar](https://steelseries.com/gg/sonar) / [SonarDeck](https://github.com/T3lluz/SonarDeck): Master, Game, Chat, Media, Aux, per-app faders, and a 10-band equaliser per category.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/T3lluz/SonusDeck/main/install.sh | bash
```

That one line installs dependencies, sets up the Game / Chat / Media / Aux sinks, keeps EasyEffects from stacking a second EQ on the mix, and starts the panel. Re-run it to update.

From a checkout instead: `./install.sh`

The script puts the app in `~/.local/share/sonusdeck`, adds `sonusdeck` and `sonusdeck-toggle` to `~/.local/bin`, and registers `Ctrl+Alt+V`, a desktop entry, and start-on-login. Toggle **Start with system** in Settings if you want that off. Append `-s -- uninstall` to the curl line to remove it.

## Use

`Ctrl+Alt+V` shows or hides the mixer.

| | |
| --- | --- |
| Left faders | Category volumes (Game / Chat / Media / Aux) and Master |
| MASTER container | Every app playing sound, each with its own fader. The panel grows to the right to fit them. |
| Category column | Drag an app strip onto Game, Chat, Media or Aux to assign it; drag a chip back to MASTER to unassign |
| Click an app strip's header | Assignment menu, if you prefer clicking over dragging |
| Sliders icon on a category | Open that category's equaliser (Back returns to the mixer) |
| **Open EasyEffects** | Extra device-wide effects on the mixed output (compressor, limiter, …) |
| Drag header / Esc | Move the panel / hide (Esc first closes the EQ page) |

EQ lives **on the category**, not in EasyEffects. SonusDeck owns Game / Chat / Media / Aux; EasyEffects is a post-mix slot and its equaliser is left off so the two don't stack. Double-click a band to zero it, scroll for 0.5 dB steps, toggle **EQ enabled** to bypass. Presets (Flat, Bass, Voice, Treble, Steps) apply a mild curve and pull the preamp down so boosts don't clip.

Assigning an app also puts it on EasyEffects' **Excluded Apps** list (and unassigning removes it) — otherwise a running EasyEffects would pull the stream back to its own sink. EasyEffects only reads that list at startup, so SonusDeck restarts it quietly when the list changes; expect a short audio gap the first time an app is assigned.

Settings, app routes, and EQ curves: `~/.config/sonusdeck/settings.json`.

## Develop

```bash
./dev.sh
```

Runs the panel from the checkout and restarts it whenever a `.py` file changes. Uses `inotifywait` when installed (`inotify-tools`), otherwise a built-in polling fallback. `Ctrl+C` shuts the panel down cleanly.

## License

MIT.
