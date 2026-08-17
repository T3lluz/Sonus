# SonusDeck

Hotkey volume mixer for PipeWire. Linux counterpart to [SteelSeries Sonar](https://steelseries.com/gg/sonar) / [SonarDeck](https://github.com/T3lluz/SonarDeck): Master, Game, Chat, Media, Aux, per-app faders, and a 10-band equaliser per category.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/T3lluz/SonusDeck/main/install.sh | bash
```

That one line installs dependencies, sets up the Game / Chat / Media / Aux sinks, tames EasyEffects (no second EQ, no stream stealing), and starts the panel. Re-run it to update.

From a checkout instead: `./install.sh`

The script puts the app in `~/.local/share/sonusdeck`, adds `sonusdeck` and `sonusdeck-toggle` to `~/.local/bin`, and registers `Ctrl+Alt+V`, a desktop entry, and start-on-login. Toggle **Start with system** in Settings if you want that off. Want a different key? Rebind the *SonusDeck Toggle* entry in your desktop's shortcut settings. Append `-s -- uninstall` to the curl line to remove it.

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

EQ lives **on the category**, not in EasyEffects. SonusDeck owns Game / Chat / Media / Aux; EasyEffects is a post-mix slot and its equaliser is left off so the two don't stack. Bands go to ±18 dB — plenty loud at the top, so manage your own headroom with the preamp when you stack big boosts. Double-click a band to zero it, scroll for 0.5 dB steps, toggle **EQ enabled** to bypass. Presets (Flat, Bass, Voice, Treble, Steps) pull the preamp down by half the peak boost: audibly louder, without hard clipping.

SonusDeck routes streams itself: assigned apps play into their category sink, everything else into EasyEffects, and the panel re-asserts those routes so nothing can silently steal a stream back. To make that possible, setup turns off EasyEffects' *process all output streams* once (a running EasyEffects is restarted quietly that one time). After that, assigning or moving an app between categories is a plain PipeWire retarget — instant, and audio keeps playing.

Settings, app routes, and EQ curves: `~/.config/sonusdeck/settings.json`.

## Develop

```bash
./dev.sh
```

Runs the panel from the checkout and restarts it whenever a `.py` file changes. Uses `inotifywait` when installed (`inotify-tools`), otherwise a built-in polling fallback. `Ctrl+C` shuts the panel down cleanly.

## License

MIT.
