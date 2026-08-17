"""The overlay panel: layout, animation, polling and state plumbing."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRect, QRectF,
    QThread, QTimer, Qt, pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QCursor, QGuiApplication, QPainter
from PyQt6.QtWidgets import QLabel, QMenu, QWidget

from .. import effects, pipewire, shortcut
from .. import theme as T
from ..config import (
    APP_NAME, CHANNELS, ROUTABLE, save_settings, set_autostart,
)
from ..graph import GraphProcess
from ..pipewire import Snapshot
from .appmixer import AppMixer
from .widgets import AppsMark, BrandMark, Card, ChipButton, IconButton, Strip, Toggle


_SPECIAL_KEYS = {
    Qt.Key.Key_Space.value: "Space",
    Qt.Key.Key_Tab.value: "Tab",
    Qt.Key.Key_Return.value: "Return",
    Qt.Key.Key_Enter.value: "Enter",
    Qt.Key.Key_Insert.value: "Ins",
    Qt.Key.Key_Delete.value: "Del",
    Qt.Key.Key_Home.value: "Home",
    Qt.Key.Key_End.value: "End",
    Qt.Key.Key_PageUp.value: "PgUp",
    Qt.Key.Key_PageDown.value: "PgDown",
    Qt.Key.Key_Up.value: "Up",
    Qt.Key.Key_Down.value: "Down",
    Qt.Key.Key_Left.value: "Left",
    Qt.Key.Key_Right.value: "Right",
}


def key_name(key: int) -> str:
    """Name a key the way KDE writes it in kglobalshortcutsrc."""
    if key in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[key]
    if Qt.Key.Key_F1.value <= key <= Qt.Key.Key_F35.value:
        return f"F{key - Qt.Key.Key_F1.value + 1}"
    if Qt.Key.Key_A.value <= key <= Qt.Key.Key_Z.value:
        return chr(key)
    if Qt.Key.Key_0.value <= key <= Qt.Key.Key_9.value:
        return chr(key)
    return ""


class Poller(QThread):
    """Reads the graph off the UI thread."""

    updated = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._running = True
        self.active = False

    def run(self) -> None:
        while self._running:
            try:
                snap = pipewire.snapshot()
                pipewire.retarget_channel_outputs(snap)
                self.updated.emit(snap)
            except Exception:
                pass
            interval = 0.7 if self.active else 2.0
            slept = 0.0
            while self._running and slept < interval:
                self.msleep(50)
                slept += 0.05

    def stop(self) -> None:
        self._running = False
        self.wait(1500)


class Panel(QWidget):
    def __init__(self, settings: dict, graph: GraphProcess) -> None:
        super().__init__(None)
        self.settings = settings
        self.graph = graph
        self.visible_now = False
        self._busy: set[str] = set()
        self._pending: dict[str, float] = {}
        self._snapshot = Snapshot()
        self._apps_w = T.APPS_VIEW_W
        self._capturing = False
        self._drawer_open = False
        self._drag_origin: QPoint | None = None
        self._routed: set[int] = set()
        self._pool = ThreadPoolExecutor(max_workers=3)

        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(T.WIN_W, T.WIN_H)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._build_header()
        self._build_strips()
        self._build_drawer()

        self._anim: QParallelAnimationGroup | None = None
        self._drawer_anim: QPropertyAnimation | None = None

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(16)
        self.frame_timer.timeout.connect(self._on_frame)

        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(40)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.timeout.connect(self._flush_pending)

        self.poller = Poller(self)
        self.poller.updated.connect(self._on_snapshot)
        self.poller.start()

    # ----- construction -------------------------------------------------

    def _build_header(self) -> None:
        y = T.TOP_PAD
        self.brand = BrandMark(26, self)
        self.brand.move(T.SIDE_PAD, y + (T.BAR_H - 26) // 2)

        self.title = QLabel("SONUS", self)
        self.title.setFont(T.semibold(16))
        self.title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.title.adjustSize()
        self.title.move(T.SIDE_PAD + 36, y + (T.BAR_H - self.title.height()) // 2)

        self.status = QLabel("", self)
        self.status.setFont(T.font(9))
        self.status.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        self.status.setFixedWidth(280)
        self.status.move(
            T.SIDE_PAD + 36 + self.title.width() + 12,
            y + (T.BAR_H - 16) // 2,
        )

        self.apps_mark = AppsMark(26, self)
        self.apps_title = QLabel("App Mixer", self)
        self.apps_title.setFont(T.semibold(16))
        self.apps_title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.apps_title.adjustSize()

        self.close_btn = IconButton("close", 32, self)
        self.close_btn.clicked.connect(self.hide_panel)
        self.gear_btn = IconButton("gear", 32, self)
        self.gear_btn.clicked.connect(self._toggle_drawer)
        self.effects_btn = ChipButton("Open EasyEffects", mark=True, parent=self)
        self.effects_btn.clicked.connect(self._open_effects)

        self._place_header()

    def _place_header(self) -> None:
        y = T.TOP_PAD
        width = self.width()
        right = width - T.SIDE_PAD
        self.close_btn.move(right - 32, y + (T.BAR_H - 32) // 2)
        self.gear_btn.move(right - 32 - 16 - 32, y + (T.BAR_H - 32) // 2)
        chip_x = right - 32 - 16 - 32 - 12 - self.effects_btn.width()
        self.effects_btn.move(chip_x, y + (T.BAR_H - self.effects_btn.height()) // 2)

        cluster_x = T.SIDE_PAD + T.SONAR_BLOCK_W + T.DIVIDER_W
        self.apps_mark.move(cluster_x, y + (T.BAR_H - 26) // 2)
        self.apps_title.move(cluster_x + 36, y + (T.BAR_H - self.apps_title.height()) // 2)

    def _build_strips(self) -> None:
        top = T.TOP_PAD + T.BAR_H + T.HEADER_GAP
        self.strips: dict[str, Strip] = {}
        x = T.SIDE_PAD
        for index, channel in enumerate(CHANNELS):
            strip = Strip(channel.key, channel.label, channel.accent, parent=self)
            strip.volumeChanged.connect(self._on_channel_volume)
            strip.muteToggled.connect(self._on_channel_mute)
            strip.move(x, top)
            self.strips[channel.key] = strip
            x += T.STRIP_W + (T.STRIP_GAP if index < len(CHANNELS) - 1 else 0)

        self.app_mixer = AppMixer(self)
        self.app_mixer.volumeChanged.connect(self._on_app_volume)
        self.app_mixer.muteToggled.connect(self._on_app_mute)
        self.app_mixer.routeRequested.connect(self.show_route_menu)
        self.app_mixer.move(T.SIDE_PAD + T.SONAR_BLOCK_W + T.DIVIDER_W, top)

    def _build_drawer(self) -> None:
        self.drawer = QWidget(self)
        self.drawer.setFixedWidth(T.SETTINGS_W)
        self.drawer.setObjectName("drawer")
        self.drawer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.drawer.setStyleSheet(
            f"#drawer {{ background-color: {T.PANEL};"
            f" border-left: 1px solid {T.RULE}; }}"
        )
        self.drawer.setFixedHeight(T.STRIP_H)
        self.drawer.move(self.width(), T.TOP_PAD + T.BAR_H + T.HEADER_GAP)

        head = QLabel("SETTINGS", self.drawer)
        head.setFont(T.semibold(13))
        head.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        head.move(20, 20)

        close = IconButton("close", 32, self.drawer)
        close.clicked.connect(self._close_drawer)
        close.move(T.SETTINGS_W - 20 - 32, 16)

        y = 62
        self.autostart_toggle, y = self._settings_row(
            y, "Start with session",
            "Launch hidden when you log in.",
            bool(self.settings.get("autostart", True)),
            self._on_autostart,
        )
        self.snap_toggle, y = self._settings_row(
            y, "Snap mouse",
            "Move the cursor to the panel when it opens.",
            bool(self.settings.get("snap_mouse", False)),
            self._on_snap,
        )
        self.graph_toggle, y = self._settings_row(
            y, "Virtual channels",
            "Provide Game, Chat, Media and Aux devices.",
            bool(self.settings.get("manage_graph", True)),
            self._on_manage_graph,
        )

        card = Card(parent=self.drawer)
        card.move(20, y)
        card.setFixedSize(T.SETTINGS_W - 40, 76)
        title = QLabel("Toggle shortcut", card)
        title.setFont(T.semibold(11))
        title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        title.move(16, 14)
        hint = QLabel("Press to show or hide the panel.", card)
        hint.setFont(T.font(9))
        hint.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        hint.setWordWrap(True)
        hint.setFixedWidth(200)
        hint.move(16, 36)
        self.hotkey_chip = ChipButton(
            self.settings.get("hotkey", "Ctrl+Alt+V"),
            surface=T.PANEL, hover=T.MUTE_HOVER, parent=card,
        )
        self.hotkey_chip.clicked.connect(self._start_capture)
        self.hotkey_chip.move(card.width() - self.hotkey_chip.width() - 16, 22)
        y += 76 + 10

        self.shortcut_note = QLabel(
            "Click the shortcut, then press a new combination. Esc cancels.",
            self.drawer,
        )
        self.shortcut_note.setFont(T.font(9))
        self.shortcut_note.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        self.shortcut_note.setWordWrap(True)
        self.shortcut_note.setFixedWidth(T.SETTINGS_W - 40)
        self.shortcut_note.move(20, y)
        y += 48

        eq_note = QLabel(
            "Equalisation lives in EasyEffects. Open it from the header and save an Autoload preset per device.",
            self.drawer,
        )
        eq_note.setFont(T.font(9))
        eq_note.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        eq_note.setWordWrap(True)
        eq_note.setFixedWidth(T.SETTINGS_W - 40)
        eq_note.move(20, y)

    def _settings_row(self, y: int, title: str, hint: str, value: bool, handler):
        card = Card(parent=self.drawer)
        card.move(20, y)
        card.setFixedSize(T.SETTINGS_W - 40, 76)
        label = QLabel(title, card)
        label.setFont(T.semibold(11))
        label.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        label.move(16, 14)
        sub = QLabel(hint, card)
        sub.setFont(T.font(9))
        sub.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        sub.setWordWrap(True)
        sub.setFixedWidth(210)
        sub.move(16, 36)
        toggle = Toggle(value, card)
        toggle.move(card.width() - Toggle.W - 16, (76 - Toggle.H) // 2)
        toggle.toggled.connect(handler)
        return toggle, y + 76 + 10

    # ----- painting -----------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(T.BG)))
        painter.drawRoundedRect(
            QRectF(0, 0, self.width(), self.height()), T.WINDOW_R, T.WINDOW_R
        )
        top = T.TOP_PAD + T.BAR_H + T.HEADER_GAP
        x = T.SIDE_PAD + T.SONAR_BLOCK_W + T.DIVIDER_W / 2
        painter.setBrush(QBrush(QColor(T.RULE)))
        painter.drawRoundedRect(QRectF(x - 1.5, top + 96, 3, T.STRIP_H - 192), 1.5, 1.5)
        painter.end()

    # ----- window behaviour ---------------------------------------------

    def _frame_width(self) -> int:
        return T.SIDE_PAD * 2 + T.SONAR_BLOCK_W + T.DIVIDER_W + self._apps_w

    def _apps_width_for(self, count: int) -> int:
        screen = self._screen_geometry()
        chrome = T.SIDE_PAD * 2 + T.SONAR_BLOCK_W + T.DIVIDER_W
        max_w = max(T.APPS_VIEW_W, screen.width() - 72 - chrome)
        max_n = max(T.APPS_VISIBLE, int((max_w + T.STRIP_GAP) // (T.APP_STRIP_W + T.STRIP_GAP)))
        show = T.APPS_VISIBLE if count <= 0 else min(max(count, T.APPS_VISIBLE), max_n)
        return show * T.APP_STRIP_W + max(0, show - 1) * T.STRIP_GAP

    def _screen_geometry(self) -> QRect:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _resize_for_apps(self, count: int) -> None:
        width = self._apps_width_for(count)
        if width == self._apps_w:
            return
        centre = self.geometry().center()
        self._apps_w = width
        self.app_mixer.set_view_width(width)
        self.setFixedSize(self._frame_width(), T.WIN_H)
        self._place_header()
        if not self._drawer_open:
            self.drawer.move(self.width(), self.drawer.y())
        else:
            self.drawer.move(self.width() - T.SETTINGS_W, self.drawer.y())
        self._move_to_centre(centre)

    def _move_to_centre(self, centre: QPoint) -> None:
        screen = self._screen_geometry()
        x = centre.x() - self.width() // 2
        y = centre.y() - self.height() // 2
        x = max(screen.left(), min(x, screen.right() - self.width()))
        y = max(screen.top(), min(y, screen.bottom() - self.height()))
        self.move(x, y)

    def _restore_position(self) -> None:
        screen = self._screen_geometry()
        x = self.settings.get("pos_x")
        y = self.settings.get("pos_y")
        if not isinstance(x, int) or not isinstance(y, int):
            x = screen.center().x() - self.width() // 2
            y = screen.center().y() - self.height() // 2
        x = max(screen.left(), min(int(x), screen.right() - self.width()))
        y = max(screen.top(), min(int(y), screen.bottom() - self.height()))
        self.move(x, y)

    def _remember_position(self) -> None:
        pos = self.pos()
        if self.settings.get("pos_x") == pos.x() and self.settings.get("pos_y") == pos.y():
            return
        self.settings["pos_x"] = pos.x()
        self.settings["pos_y"] = pos.y()
        save_settings(self.settings)

    def show_panel(self) -> None:
        if self.visible_now:
            return
        self.visible_now = True
        self.poller.active = True
        for strip in self.strips.values():
            strip.reset_display()
        self.app_mixer.reset_display()
        self._restore_position()

        resting = self.pos()
        self.setWindowOpacity(0.0)
        self.move(resting + QPoint(0, T.RISE_PX))
        self.show()
        self.raise_()
        self.activateWindow()
        self._animate(resting + QPoint(0, T.RISE_PX), resting, 0.0, 1.0, T.SHOW_MS, closing=False)
        self._pool.submit(self._refresh_now)
        self.frame_timer.start()
        if self.settings.get("snap_mouse"):
            QTimer.singleShot(T.SHOW_MS, self._snap_cursor)

    def hide_panel(self) -> None:
        if not self.visible_now:
            return
        if self._drawer_open:
            self._close_drawer(animate=False)
        self._stop_capture()
        self._remember_position()
        self.visible_now = False
        self.poller.active = False
        resting = self.pos()
        self._animate(resting, resting + QPoint(0, T.RISE_PX), 1.0, 0.0, T.HIDE_MS, closing=True)

    def toggle(self) -> None:
        if self.visible_now:
            self.hide_panel()
        else:
            self.show_panel()

    def _animate(
        self,
        start: QPoint,
        end: QPoint,
        from_op: float,
        to_op: float,
        duration: int,
        closing: bool,
    ) -> None:
        if self._anim is not None:
            self._anim.stop()
        group = QParallelAnimationGroup(self)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(duration)
        slide.setStartValue(start)
        slide.setEndValue(end)
        slide.setEasingCurve(
            QEasingCurve.Type.InCubic if closing else QEasingCurve.Type.OutCubic
        )
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(duration)
        fade.setStartValue(from_op)
        fade.setEndValue(to_op)
        group.addAnimation(slide)
        group.addAnimation(fade)

        def finished() -> None:
            self._anim = None
            if closing:
                self.hide()
                self.setWindowOpacity(1.0)
                self.move(end - QPoint(0, T.RISE_PX))
                self.frame_timer.stop()

        group.finished.connect(finished)
        self._anim = group
        group.start()

    def _snap_cursor(self) -> None:
        if self.visible_now:
            QCursor.setPos(self.geometry().center())

    # ----- drawer --------------------------------------------------------

    def _toggle_drawer(self) -> None:
        if self._drawer_open:
            self._close_drawer()
        else:
            self._open_drawer()

    def _open_drawer(self) -> None:
        self._drawer_open = True
        self.drawer.show()
        self.drawer.raise_()
        self._slide_drawer(self.width() - T.SETTINGS_W)

    def _close_drawer(self, animate: bool = True) -> None:
        self._drawer_open = False
        self._stop_capture()
        if animate:
            self._slide_drawer(self.width())
        else:
            self.drawer.move(self.width(), self.drawer.y())

    def _slide_drawer(self, target_x: int) -> None:
        if self._drawer_anim is not None:
            self._drawer_anim.stop()
        anim = QPropertyAnimation(self.drawer, b"pos", self)
        anim.setDuration(T.DRAWER_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(self.drawer.pos())
        anim.setEndValue(QPoint(target_x, self.drawer.y()))
        anim.finished.connect(lambda: setattr(self, "_drawer_anim", None))
        self._drawer_anim = anim
        anim.start()

    # ----- settings handlers ---------------------------------------------

    def _persist(self) -> None:
        save_settings(self.settings)

    def _on_autostart(self, value: bool) -> None:
        self.settings["autostart"] = value
        set_autostart(value)
        self._persist()

    def _on_snap(self, value: bool) -> None:
        self.settings["snap_mouse"] = value
        self._persist()

    def _on_manage_graph(self, value: bool) -> None:
        self.settings["manage_graph"] = value
        self._persist()
        if value:
            self._pool.submit(self.graph.start)
        else:
            self._pool.submit(self.graph.stop)

    def _open_effects(self) -> None:
        if not effects.launch():
            self.status.setText("EasyEffects not installed")

    def _start_capture(self) -> None:
        self._capturing = True
        self.hotkey_chip.set_text("Press keys…")
        self.setFocus()

    def _stop_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        self.hotkey_chip.set_text(self.settings.get("hotkey", "Ctrl+Alt+V"))

    def keyPressEvent(self, event) -> None:
        if self._capturing:
            self._capture_key(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._drawer_open:
                self._close_drawer()
            else:
                self.hide_panel()
            return
        super().keyPressEvent(event)

    def _capture_key(self, event) -> None:
        key = event.key()
        if key in (
            Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta,
        ):
            return
        if key == Qt.Key.Key_Escape:
            self._stop_capture()
            return
        mods = event.modifiers()
        if not (
            mods & Qt.KeyboardModifier.ControlModifier
            or mods & Qt.KeyboardModifier.AltModifier
            or mods & Qt.KeyboardModifier.MetaModifier
        ):
            return
        name = key_name(key)
        if not name:
            return
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("Meta")
        parts.append(name)
        sequence = "+".join(parts)
        self._capturing = False
        self.settings["hotkey"] = sequence
        self._persist()
        self.hotkey_chip.set_text(sequence)
        ok, detail = shortcut.install(sequence)
        self.shortcut_note.setText(
            detail if ok else f"Could not register: {detail}"
        )

    # ----- audio state ----------------------------------------------------

    def _on_snapshot(self, snap: Snapshot) -> None:
        self._snapshot = snap
        if not self.visible_now:
            return
        for channel in CHANNELS:
            state = snap.channels.get(channel.key)
            strip = self.strips[channel.key]
            if state is None or channel.key in self._busy:
                continue
            strip.apply_state(state.volume, state.muted)
        self._resize_for_apps(len(snap.apps))
        self.app_mixer.apply(snap.apps, self._busy)
        self._apply_saved_routes(snap)
        self._update_status(snap)

    def _apply_saved_routes(self, snap: Snapshot) -> None:
        """Send a stream back to the channel it was assigned to last time."""
        routes = self.settings.get("routes") or {}
        if not routes:
            return
        live = {app.serial for app in snap.apps}
        self._routed.intersection_update(live)
        for app in snap.apps:
            wanted = routes.get(app.binary or app.key)
            if not wanted or wanted == app.channel or app.serial in self._routed:
                continue
            channel = next((c for c in ROUTABLE if c.key == wanted), None)
            if channel is None:
                continue
            self._routed.add(app.serial)
            self._pool.submit(pipewire.move_stream, app.serial, channel.node_name)

    def _update_status(self, snap: Snapshot) -> None:
        if not snap.channels:
            self.status.setText("Starting channels…")
            return
        missing = [c.label.title() for c in CHANNELS if c.kind != "master" and c.key not in snap.channels]
        if missing:
            self.status.setText("Missing: " + ", ".join(missing))
            return
        self.status.setText(effects.status_text())

    def _refresh_now(self) -> None:
        try:
            snap = pipewire.snapshot()
            pipewire.retarget_channel_outputs(snap)
        except Exception:
            return
        self.poller.updated.emit(snap)

    def _node_for(self, key: str) -> int | None:
        state = self._snapshot.channels.get(key)
        return state.node_id if state else None

    def _on_channel_volume(self, key: str, value: float) -> None:
        self._busy.add(key)
        self._pending[key] = value
        self.flush_timer.start()

    def _flush_pending(self) -> None:
        pending, self._pending = self._pending, {}
        for key, value in pending.items():
            node = self._node_for(key)
            if node is None:
                app = next((a for a in self._snapshot.apps if a.key == key), None)
                if app is None:
                    self._busy.discard(key)
                    continue
                targets = app.members or [app.node_id]
                self._pool.submit(self._write_volume, key, targets, value)
                continue
            self._pool.submit(self._write_volume, key, [node], value)

    def _write_volume(self, key: str, nodes: list[int], value: float) -> None:
        try:
            for node in nodes:
                pipewire.set_volume(node, value)
        finally:
            self._busy.discard(key)

    def _on_channel_mute(self, key: str) -> None:
        strip = self.strips.get(key)
        node = self._node_for(key)
        if strip is None or node is None:
            return
        state = not strip.muted
        strip.set_muted(state)
        self._busy.add(key)
        self._pool.submit(self._write_mute, key, [node], state)

    def _write_mute(self, key: str, nodes: list[int], state: bool) -> None:
        try:
            for node in nodes:
                pipewire.set_mute(node, state)
        finally:
            self._busy.discard(key)

    def _on_app_volume(self, key: str, value: float) -> None:
        self._busy.add(key)
        self._pending[key] = value
        self.flush_timer.start()

    def _on_app_mute(self, key: str) -> None:
        strip = self.app_mixer.strips.get(key)
        app = next((a for a in self._snapshot.apps if a.key == key), None)
        if strip is None or app is None:
            return
        state = not strip.muted
        strip.set_muted(state)
        self._busy.add(key)
        self._pool.submit(self._write_mute, key, app.members or [app.node_id], state)

    def route_app(self, key: str, channel_key: str) -> None:
        app = next((a for a in self._snapshot.apps if a.key == key), None)
        if app is None:
            return
        routes = dict(self.settings.get("routes") or {})
        if not channel_key:
            routes.pop(app.binary or app.key, None)
            self.settings["routes"] = routes
            self._persist()
            target = self._snapshot.mix_target or self._snapshot.default_sink
            if target:
                self._pool.submit(pipewire.move_stream, app.serial, target)
            return
        channel = next((c for c in ROUTABLE if c.key == channel_key), None)
        if channel is None:
            return
        routes[app.binary or app.key] = channel_key
        self.settings["routes"] = routes
        self._persist()
        self._pool.submit(pipewire.move_stream, app.serial, channel.node_name)

    def show_route_menu(self, key: str, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {T.PANEL}; color: {T.TEXT}; border: 1px solid {T.RULE};"
            f" padding: 6px; }}"
            f"QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}"
            f"QMenu::item:selected {{ background: {T.MUTE_HOVER}; }}"
        )
        for channel in ROUTABLE:
            action = menu.addAction(f"Send to {channel.label.title()}")
            action.triggered.connect(
                lambda _checked, c=channel.key, k=key: self.route_app(k, c)
            )
        menu.addSeparator()
        reset = menu.addAction("Send to output (EasyEffects)")
        reset.triggered.connect(lambda _checked, k=key: self.route_app(k, ""))
        menu.exec(global_pos)

    # ----- frame loop -----------------------------------------------------

    def _on_frame(self) -> None:
        now = time.perf_counter()
        busy = False
        for strip in self.strips.values():
            busy = strip.tick(now) or busy
        busy = self.app_mixer.tick(now) or busy

    # ----- dragging -------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < T.TOP_PAD + T.BAR_H:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_origin is not None:
            self._drag_origin = None
            self._remember_position()

    def closeEvent(self, event) -> None:
        self.poller.stop()
        self._pool.shutdown(wait=False)
        super().closeEvent(event)
