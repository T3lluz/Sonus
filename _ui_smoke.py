"""Offscreen smoke test: vertical bins, real mouse-event drag, blocklist edits.

Run:  QT_QPA_PLATFORM=offscreen python _ui_smoke.py
"""

from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
import traceback
from pathlib import Path

faulthandler.enable()

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "_shots"
SHOTS.mkdir(exist_ok=True)
_tmp = tempfile.mkdtemp(prefix="sonus_smoke_", dir=str(ROOT / "_shots"))
os.environ["XDG_CONFIG_HOME"] = str(Path(_tmp) / "xdg")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    tag = "ok  " if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ----- blocklist unit checks (no Qt needed) --------------------------------

def test_blocklist() -> None:
    from sonusdeck import effects

    rc = Path(_tmp) / "easyeffectsrc"
    rc.write_text(
        "[Presets]\n"
        "lastLoadedOutputPreset=SonusDeck Aux\n"
        "\n"
        "[StreamOutputs]\n"
        "outputDevice=some_sink\n"
        "showBlocklistedApps=true\n"
        "\n"
        "[Window]\n"
        "height=711\n",
        encoding="utf-8",
    )

    # Patch rc_paths so tests never touch the real config.
    effects.rc_paths = lambda: [rc]

    check("read empty blocklist", effects.read_excluded() == [])

    ok = effects._edit_exclusions(["spotify", "vesktop"], [])
    text = rc.read_text()
    check("insert into existing group", ok and "blocklist=spotify,vesktop" in text)
    check("insert placed inside [StreamOutputs]",
          text.index("[StreamOutputs]") < text.index("blocklist=") < text.index("[Window]"))

    ok = effects._edit_exclusions(["Firefox"], ["spotify"])
    check("add+remove", ok and effects.read_excluded() == ["vesktop", "Firefox"])

    ok = effects._edit_exclusions(["we,ird\\name"], [])
    check("comma/backslash escaping roundtrip",
          ok and "we,ird\\name" in effects.read_excluded())

    ok = effects._edit_exclusions(["Firefox"], [])
    check("no-op when already present", not ok)

    check("other keys preserved",
          "outputDevice=some_sink" in rc.read_text()
          and "lastLoadedOutputPreset=SonusDeck Aux" in rc.read_text())

    # apply_exclusions with EE not running: edits the file, no restart.
    effects.running = lambda: False
    restarted = effects.apply_exclusions(["mpv"], [])
    check("apply without EE running edits file, no restart",
          not restarted and "mpv" in effects.read_excluded())


def test_passthrough() -> None:
    from sonusdeck import effects

    out_dir = Path(_tmp) / "xdg" / "easyeffects" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    rc = Path(_tmp) / "xdg" / "easyeffects" / "db" / "easyeffectsrc"
    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.write_text(
        "[Presets]\nlastLoadedOutputPreset=SonusDeck Aux\n\n"
        "[StreamOutputs]\nblocklist=\n",
        encoding="utf-8",
    )
    (out_dir / "SonusDeck Aux.json").write_text(
        '{"output": {"plugins_order": ["equalizer#0"], "equalizer#0": {"bypass": false}}}\n',
        encoding="utf-8",
    )
    (out_dir / "SonusDeck Game.json").write_text("{}\n", encoding="utf-8")
    eqdb = Path(_tmp) / "xdg" / "easyeffects" / "db" / "equalizerrc"
    eqdb.write_text("[soe][Equalizer#0#left]\nband0Gain=12\nband1Gain=7.5\n", encoding="utf-8")

    effects.preset_dirs = lambda: [out_dir]
    effects.rc_paths = lambda: [rc]
    effects._equalizer_db_paths = lambda: [eqdb]
    effects.running = lambda: False

    reloaded = effects.ensure_passthrough()
    check("passthrough does not reload when EE is stopped", not reloaded)
    check("category presets removed",
          not (out_dir / "SonusDeck Aux.json").exists()
          and not (out_dir / "SonusDeck Game.json").exists())
    mix = out_dir / "SonusDeck Mix.json"
    check("mix preset written", mix.exists())
    payload = mix.read_text()
    check("mix has no equalizer", "equalizer" not in payload)
    check("last loaded switched to Mix",
          "lastLoadedOutputPreset=SonusDeck Mix" in rc.read_text())
    check("equalizer db flattened",
          "band0Gain=0" in eqdb.read_text() and "band1Gain=0" in eqdb.read_text())

    # Already on Mix, EE still stopped: second call is a no-op write-wise.
    reloaded = effects.ensure_passthrough()
    check("second passthrough still quiet", not reloaded)


# ----- UI checks ------------------------------------------------------------

def test_ui() -> None:
    from PyQt6.QtCore import QPoint, QPointF, QRect, QEvent, Qt, QTimer
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication

    from sonusdeck import theme as T
    from sonusdeck.config import load_settings
    from sonusdeck.pipewire import AppStream, Snapshot
    from sonusdeck.ui.panel import Panel

    app = QApplication(sys.argv)

    class FakeGraph:
        def poll(self):
            return None

        def stop(self):
            pass

    settings = load_settings()
    settings["manage_graph"] = False
    panel = Panel(settings, FakeGraph())
    panel.poller.stop()

    apps = [
        AppStream(key=f"app{i}", name=name, node_id=100 + i, serial=1000 + i,
                  volume=0.5 + i * 0.1, icon_name="", binary=name.lower(),
                  channel="", members=[100 + i], node_names=[name])
        for i, name in enumerate(["Firefox", "Spotify", "Vesktop", "mpv"])
    ]
    snap = Snapshot(
        channels={}, apps=apps, default_sink="dummy",
        mix_target="easyeffects_sink", ready=True,
    )
    panel.visible_now = True
    panel.show()
    panel._screen_geometry = lambda: QRect(0, 0, 2560, 1440)
    panel._on_snapshot(snap)
    app.processEvents()

    mixer = panel.app_mixer

    # Layout: bins in a left column, master beside them, full height.
    bins = [mixer.bins[k] for k in mixer.bins]
    check("bins are stacked vertically",
          all(b.x() == 0 for b in bins)
          and all(bins[i].geometry().bottom() < bins[i + 1].y() for i in range(len(bins) - 1)))
    check("bin column width", all(b.width() == T.BIN_COL_W for b in bins))
    check("bins span full height",
          abs(bins[-1].geometry().bottom() + 1 - T.STRIP_H) <= 1,
          f"bottom={bins[-1].geometry().bottom()}")
    check("master starts right of bins",
          mixer.master.x() == T.MC_X and mixer.master.height() == T.STRIP_H)
    check("bins have no eq icon",
          all(not hasattr(b, "eq_active") and not hasattr(b, "_eq_rect") for b in bins))

    inner = 4 * T.APP_STRIP_W + 3 * T.STRIP_GAP
    check("panel sized to app count",
          mixer.master.width() == T.MC_PAD * 2 + inner,
          f"master_w={mixer.master.width()} want={T.MC_PAD * 2 + inner}")
    check("no spare app slot",
          mixer.master._content_w() == mixer.master._viewport_w(),
          f"content={mixer.master._content_w()} view={mixer.master._viewport_w()}")
    strip = mixer.strips["app1"]  # Spotify
    m = strip.m
    check("app fader is long", (m.fader_bot - m.fader_top) >= 260,
          f"len={m.fader_bot - m.fader_top}")
    # The % text rect spans pct_y ± 11; the thumb's top at max is
    # fader_top - thumb_h / 2. Require a positive gap between them.
    check("pct label clear of thumb", m.fader_top - m.thumb_h / 2 > m.pct_y + 11)

    panel.grab().save(str(SHOTS / "sonus_mixer.png"))

    # Drag Spotify onto the Game bin using real mouse events on the Strip.
    assigned: list[tuple[str, str]] = []
    mixer.assignRequested.connect(lambda k, c: assigned.append((k, c)))

    def press_at(widget, local: QPointF) -> None:
        ev = QMouseEvent(QEvent.Type.MouseButtonPress, local,
                         widget.mapToGlobal(local.toPoint()).toPointF(),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        widget.mousePressEvent(ev)

    def move_to(widget, global_pt: QPoint, local: QPointF) -> None:
        ev = QMouseEvent(QEvent.Type.MouseMove, local, QPointF(global_pt),
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        widget.mouseMoveEvent(ev)

    def release_at(widget, global_pt: QPoint, local: QPointF) -> None:
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, local, QPointF(global_pt),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        widget.mouseReleaseEvent(ev)

    header = QPointF(strip.width() / 2, 30)  # icon/header area, not the fader
    game_bin = mixer.bins["game"]
    target_global = game_bin.mapToGlobal(QPoint(game_bin.width() // 2,
                                                game_bin.height() // 2))
    press_at(strip, header)
    for step in range(1, 6):
        start = strip.mapToGlobal(header.toPoint())
        pt = QPoint(
            start.x() + (target_global.x() - start.x()) * step // 5,
            start.y() + (target_global.y() - start.y()) * step // 5,
        )
        move_to(strip, pt, header)
        app.processEvents()
    check("ghost visible during drag", mixer._ghost is not None)
    check("game bin highlights", game_bin.drop_hover)
    panel.grab().save(str(SHOTS / "sonus_drag.png"))
    release_at(strip, target_global, header)
    app.processEvents()

    check("drop assigns app to game", assigned == [("app1", "game")],
          f"assigned={assigned}")
    check("ghost cleaned up", mixer._ghost is None)

    # Pending state should show Spotify's chip in the Game bin immediately.
    mixer.apply(apps, set())
    app.processEvents()
    check("chip appears in game bin",
          any(item[0] == "app1" for item in game_bin._apps))
    panel.grab().save(str(SHOTS / "sonus_after_drop.png"))

    # Drag the chip back to master to unassign.
    chip_rect = game_bin._chip_rects[0]
    chip_local = QPointF(chip_rect.center())
    master_global = mixer.master.mapToGlobal(QPoint(mixer.master.width() // 2,
                                                    mixer.master.height() // 2))
    press_at(game_bin, chip_local)
    move_to(game_bin, game_bin.mapToGlobal(chip_local.toPoint()) + QPoint(30, 0),
            chip_local)
    move_to(game_bin, master_global, chip_local)
    app.processEvents()
    check("master highlights on chip drag", mixer.master.drop_hover)
    release_at(game_bin, master_global, chip_local)
    app.processEvents()
    check("chip drop back unassigns", assigned[-1] == ("app1", ""),
          f"assigned={assigned}")

    # EQ page still works after the layout change.
    panel._on_eq_clicked("game")
    import time as _time
    end = _time.monotonic() + 0.8
    while _time.monotonic() < end:
        app.processEvents()
        _time.sleep(0.02)
    panel.grab().save(str(SHOTS / "sonus_eqpage.png"))
    check("eq page open", panel._eq_open and panel.eq_panel.isVisible())
    panel._close_eq_panel(animate=False)


def main() -> int:
    try:
        test_blocklist()
        test_passthrough()
        test_ui()
    except Exception:
        traceback.print_exc()
        FAILURES.append("exception")
    print("PASS" if not FAILURES else f"FAILED: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
