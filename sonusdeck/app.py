"""Process entry point and command line."""

from __future__ import annotations

import argparse
import os
import sys


def _select_platform() -> None:
    """Prefer XWayland.

    Wayland clients cannot position their own windows, which a panel that
    remembers where you put it needs to do. XWayland gives us that back, so use
    it whenever an X display is reachable.
    """
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sonusdeck", description="Hotkey volume panel for PipeWire")
    parser.add_argument("--toggle", action="store_true", help="show or hide the running panel")
    parser.add_argument("--autostart", action="store_true", help="start hidden")
    parser.add_argument("--quit", action="store_true", help="stop the running panel")
    parser.add_argument("--install-shortcut", action="store_true", help="register the global shortcut and exit")
    parser.add_argument("--no-graph", action="store_true", help="do not create virtual channels")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    _select_platform()

    from PyQt6.QtWidgets import QApplication

    from . import ipc, shortcut
    from .config import APP_NAME, load_settings, save_settings, set_autostart, write_launch_desktop
    from .graph import GraphProcess
    from .ui.panel import Panel

    settings = load_settings()

    if args.install_shortcut:
        QApplication(sys.argv[:1])
        ok, detail = shortcut.install(settings.get("hotkey", "Ctrl+Alt+V"))
        print(detail)
        return 0 if ok else 1

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    if args.quit:
        return 0 if ipc.send("quit") else 1

    if ipc.instance_running():
        # Another panel owns the socket: hand the request over and step aside.
        if args.toggle or not args.autostart:
            ipc.send("toggle")
        return 0

    server = ipc.CommandServer()
    if not server.listen():
        print("could not claim the control socket", file=sys.stderr)
        return 1

    graph = GraphProcess()
    manage = bool(settings.get("manage_graph", True)) and not args.no_graph
    if manage:
        graph.start()

    set_autostart(bool(settings.get("autostart", True)))
    write_launch_desktop()
    wanted = settings.get("hotkey")
    # Reinstalling restarts kglobalaccel, so only do it when the binding moved.
    if wanted and shortcut.normalise(wanted) != shortcut.current():
        shortcut.install(wanted)

    panel = Panel(settings, graph)
    server.toggled.connect(panel.toggle)

    def shutdown() -> None:
        save_settings(panel.settings)
        panel.hide()
        panel.poller.stop()
        graph.stop()
        server.close()
        app.quit()

    server.quit_requested.connect(shutdown)
    app.aboutToQuit.connect(graph.stop)

    if not args.autostart:
        panel.show_panel()

    return app.exec()
