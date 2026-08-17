"""The overlay panel: layout, animation, polling and state plumbing."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRect, QRectF,
    QThread, QTimer, Qt, pyqtSignal,
)
from PyQt6.QtGui import QBrush, QColor, QCursor, QGuiApplication, QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QMenu, QWidget

from .. import effects, pipewire
from .. import eq as eqmod
from .. import theme as T
from ..config import (
    APP_NAME, CHANNELS, HOTKEY, ROUTABLE, SINK_CHANNELS, save_settings, set_autostart,
)
from ..graph import GraphProcess
from ..pipewire import Snapshot
from .appmixer import AppMixer
from .eqpanel import EqPanel
from .widgets import AppsMark, BrandMark, Card, ChipButton, IconButton, Strip, Toggle


class Poller(QThread):
    """Reads the graph off the UI thread and keeps routes asserted."""

    updated = pyqtSignal(object)

    def __init__(self, routes_provider, parent=None) -> None:
        super().__init__(parent)
        self._running = True
        self._routes = routes_provider
        self.active = False

    def run(self) -> None:
        while self._running:
            try:
                snap = pipewire.snapshot()
                pipewire.retarget_channel_outputs(snap)
                pipewire.enforce_app_routes(snap, self._routes())
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
        self._drawer_open = False
        self._eq_open = False
        self._drag_origin: QPoint | None = None
        self._pool = ThreadPoolExecutor(max_workers=3)

        self._eq_keys = tuple(c.key for c in SINK_CHANNELS)
        self._eqs = eqmod.load_all(settings, self._eq_keys)
        self._eq_applied: dict[str, tuple] = {}

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
        self._build_eq_panel()

        self._anim: QParallelAnimationGroup | None = None
        self._drawer_anim: QPropertyAnimation | None = None
        self._eq_anim: QParallelAnimationGroup | None = None

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(16)
        self.frame_timer.timeout.connect(self._on_frame)

        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(40)
        self.flush_timer.setSingleShot(True)
        self.flush_timer.timeout.connect(self._flush_pending)

        # Live EQ pushes are cheap; persisting and mirroring to EasyEffects
        # waits until the sliders settle.
        self.eq_apply_timer = QTimer(self)
        self.eq_apply_timer.setInterval(60)
        self.eq_apply_timer.setSingleShot(True)
        self.eq_apply_timer.timeout.connect(self._flush_eq)

        self.eq_persist_timer = QTimer(self)
        self.eq_persist_timer.setInterval(800)
        self.eq_persist_timer.setSingleShot(True)
        self.eq_persist_timer.timeout.connect(self._persist_eq)

        self.poller = Poller(self._current_routes, self)
        self.poller.updated.connect(self._on_snapshot)
        self.poller.start()

    def _current_routes(self) -> dict[str, str]:
        routes = self.settings.get("routes")
        return dict(routes) if isinstance(routes, dict) else {}

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
            strip = Strip(
                channel.key, channel.label, channel.accent,
                eq_icon=channel.kind == "sink", parent=self,
            )
            strip.volumeChanged.connect(self._on_channel_volume)
            strip.muteToggled.connect(self._on_channel_mute)
            strip.eqClicked.connect(self._on_eq_clicked)
            state = self._eqs.get(channel.key)
            if state is not None:
                strip.set_eq_active(state.enabled and not state.is_flat())
            strip.move(x, top)
            self.strips[channel.key] = strip
            x += T.STRIP_W + (T.STRIP_GAP if index < len(CHANNELS) - 1 else 0)

        self.app_mixer = AppMixer(self)
        self.app_mixer.volumeChanged.connect(self._on_app_volume)
        self.app_mixer.muteToggled.connect(self._on_app_mute)
        self.app_mixer.routeRequested.connect(self.show_route_menu)
        self.app_mixer.assignRequested.connect(self.route_app)
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
            y, "Start with system",
            "Launch hidden when the system starts.",
            bool(self.settings.get("autostart", True)),
            self._on_autostart,
        )
        self.graph_toggle, y = self._settings_row(
            y, "Virtual channels",
            "Provide Game, Chat, Media and Aux devices.",
            bool(self.settings.get("manage_graph", True)),
            self._on_manage_graph,
        )

        shortcut_note = QLabel(
            f"{HOTKEY} shows or hides the panel. To use a different key, "
            f"rebind the \u201c{APP_NAME} Toggle\u201d entry in your desktop's "
            "shortcut settings.",
            self.drawer,
        )
        shortcut_note.setFont(T.font(9))
        shortcut_note.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        shortcut_note.setWordWrap(True)
        shortcut_note.setFixedWidth(T.SETTINGS_W - 40)
        shortcut_note.move(20, y)
        y += 60

        eq_note = QLabel(
            "Each category has its own equaliser: click the sliders icon on Game, "
            "Chat, Media or Aux. EasyEffects stays a post-mix slot for extra "
            "effects — its equaliser is left off so the two don't stack.",
            self.drawer,
        )
        eq_note.setFont(T.font(9))
        eq_note.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        eq_note.setWordWrap(True)
        eq_note.setFixedWidth(T.SETTINGS_W - 40)
        eq_note.move(20, y)

    def _build_eq_panel(self) -> None:
        """The EQ is a full page sliding over the deck, not a drawer."""
        self.eq_panel = EqPanel(self)
        self.eq_panel.set_page_width(self._page_width())
        self.eq_panel.move(self.width(), self._content_top())
        self.eq_panel.hide()
        self._eq_fx = QGraphicsOpacityEffect(self.eq_panel)
        self._eq_fx.setOpacity(1.0)
        self.eq_panel.setGraphicsEffect(self._eq_fx)
        self.eq_panel.eqChanged.connect(self._on_eq_changed)
        self.eq_panel.backRequested.connect(self._close_eq_panel)

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

    def _content_top(self) -> int:
        return T.TOP_PAD + T.BAR_H + T.HEADER_GAP

    def _page_width(self) -> int:
        return self._frame_width() - T.SIDE_PAD * 2

    def _min_apps_width(self) -> int:
        """Narrowest apps block whose header row still fits.

        The row holds the apps mark + "App Mixer" on the left and the
        EasyEffects chip, gear and close buttons on the right.
        """
        title_w = 36 + self.apps_title.width()
        controls_w = self.effects_btn.width() + 12 + 32 + 16 + 32
        return title_w + 24 + controls_w

    def _apps_width_for(self, count: int) -> int:
        screen = self._screen_geometry()
        chrome = T.SIDE_PAD * 2 + T.SONAR_BLOCK_W + T.DIVIDER_W
        inner_chrome = T.MC_X + T.MC_PAD * 2
        max_inner = max(T.APP_STRIP_W, screen.width() - 72 - chrome - inner_chrome)
        if count <= 0:
            inner = min(T.EMPTY_MASTER_INNER, max_inner)
        else:
            needed = count * T.APP_STRIP_W + max(0, count - 1) * T.STRIP_GAP
            if needed <= max_inner:
                inner = needed
            else:
                n = max(1, int((max_inner + T.STRIP_GAP) // (T.APP_STRIP_W + T.STRIP_GAP)))
                inner = n * T.APP_STRIP_W + max(0, n - 1) * T.STRIP_GAP
        return max(inner_chrome + inner, self._min_apps_width())

    def _screen_geometry(self) -> QRect:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _resize_for_apps(self, count: int) -> None:
        width = self._apps_width_for(count)
        new_w = T.SIDE_PAD * 2 + T.SONAR_BLOCK_W + T.DIVIDER_W + width
        if width == self._apps_w and self.width() == new_w:
            return
        left, top = self.x(), self.y()
        self._apps_w = width
        self.app_mixer.set_view_width(width)
        self.setFixedSize(new_w, T.WIN_H)
        self._place_header()
        if not self._drawer_open:
            self.drawer.move(self.width(), self.drawer.y())
        else:
            self.drawer.move(self.width() - T.SETTINGS_W, self.drawer.y())
        self.eq_panel.set_page_width(self._page_width())
        if self._eq_open:
            self.eq_panel.move(T.SIDE_PAD, self.eq_panel.y())
        else:
            self.eq_panel.move(self.width(), self.eq_panel.y())
        screen = self._screen_geometry()
        if left + new_w > screen.right():
            left = max(screen.left(), screen.right() - new_w)
        if left < screen.left():
            left = screen.left()
        self.move(left, top)

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
        self._resize_for_apps(len(self._snapshot.apps))

        resting = self.pos()
        self.setWindowOpacity(0.0)
        self.move(resting + QPoint(0, T.RISE_PX))
        self.show()
        self.raise_()
        self.activateWindow()
        self._animate(resting + QPoint(0, T.RISE_PX), resting, 0.0, 1.0, T.SHOW_MS, closing=False)
        self._pool.submit(self._refresh_now)
        self.frame_timer.start()

    def hide_panel(self) -> None:
        if not self.visible_now:
            return
        if self._drawer_open:
            self._close_drawer(animate=False)
        if self._eq_open:
            self._close_eq_panel(animate=False)
        self._persist_eq()
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

    # ----- drawer --------------------------------------------------------

    def _toggle_drawer(self) -> None:
        if self._drawer_open:
            self._close_drawer()
        else:
            self._open_drawer()

    def _open_drawer(self) -> None:
        if self._eq_open:
            self._close_eq_panel(animate=False)
        self._drawer_open = True
        self.drawer.show()
        self.drawer.raise_()
        self._slide_drawer(self.width() - T.SETTINGS_W)

    def _close_drawer(self, animate: bool = True) -> None:
        self._drawer_open = False
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

    # ----- equaliser -------------------------------------------------------

    def _on_eq_clicked(self, key: str) -> None:
        """EQ icon on a category strip opens that category's equaliser."""
        if key not in self._eq_keys:
            return
        if self._eq_open and self.eq_panel.channel_key == key:
            self._close_eq_panel()
        else:
            self._open_eq_panel(key)

    def _open_eq_panel(self, key: str) -> None:
        channel = next((c for c in SINK_CHANNELS if c.key == key), None)
        if channel is None:
            return
        if self._drawer_open:
            self._close_drawer(animate=False)
        state = self._eqs.setdefault(key, eqmod.ChannelEq())
        already_open = self._eq_open
        self.eq_panel.set_page_width(self._page_width())
        self.eq_panel.set_channel(key, channel.label, channel.accent, state)
        self._eq_open = True
        self.eq_panel.show()
        self.eq_panel.raise_()
        if already_open:
            self.eq_panel.move(T.SIDE_PAD, self.eq_panel.y())
        else:
            # Slide the page in from the right edge while fading it in.
            self.eq_panel.move(self.width(), self.eq_panel.y())
            self._slide_eq(T.SIDE_PAD, 0.0, 1.0)

    def _close_eq_panel(self, animate: bool = True) -> None:
        if not self._eq_open:
            return
        self._eq_open = False
        self._persist_eq()
        if animate:
            self._slide_eq(self.width(), 1.0, 0.0, hide_after=True)
        else:
            self.eq_panel.hide()
            self.eq_panel.move(self.width(), self.eq_panel.y())

    def _slide_eq(
        self, target_x: int, from_op: float, to_op: float, hide_after: bool = False
    ) -> None:
        if self._eq_anim is not None:
            self._eq_anim.stop()
        group = QParallelAnimationGroup(self)
        slide = QPropertyAnimation(self.eq_panel, b"pos", self)
        slide.setDuration(T.PAGE_MS)
        slide.setEasingCurve(
            QEasingCurve.Type.InCubic if hide_after else QEasingCurve.Type.OutCubic
        )
        slide.setStartValue(self.eq_panel.pos())
        slide.setEndValue(QPoint(target_x, self.eq_panel.y()))
        group.addAnimation(slide)
        fade = QPropertyAnimation(self._eq_fx, b"opacity", self)
        fade.setDuration(T.PAGE_MS)
        fade.setStartValue(from_op)
        fade.setEndValue(to_op)
        group.addAnimation(fade)

        def finished() -> None:
            self._eq_anim = None
            if hide_after and not self._eq_open:
                self.eq_panel.hide()
                self._eq_fx.setOpacity(1.0)

        group.finished.connect(finished)
        self._eq_anim = group
        group.start()

    def _on_eq_changed(self, key: str) -> None:
        self._eqs[key] = self.eq_panel.current_state()
        state = self._eqs[key]
        active = state.enabled and not state.is_flat()
        strip = self.strips.get(key)
        if strip is not None:
            strip.set_eq_active(active)
        self.eq_apply_timer.start()
        self.eq_persist_timer.start()

    def _flush_eq(self) -> None:
        """Push the edited category EQ into its live filter-chain node."""
        key = self.eq_panel.channel_key
        state = self._eqs.get(key)
        node = self._node_for(key)
        if state is None or node is None:
            return
        self._eq_applied[key] = (node, state.signature())
        self._pool.submit(pipewire.set_channel_eq, node, state)

    def _persist_eq(self) -> None:
        self.eq_persist_timer.stop()
        eqmod.store_all(self.settings, self._eqs)
        self._persist()

    def _ensure_eq_applied(self, snap: Snapshot) -> None:
        """Re-push saved EQs whenever a channel node (re)appears."""
        for channel in SINK_CHANNELS:
            node_state = snap.channels.get(channel.key)
            state = self._eqs.get(channel.key)
            if node_state is None or state is None:
                continue
            wanted = (node_state.node_id, state.signature())
            if self._eq_applied.get(channel.key) != wanted:
                self._eq_applied[channel.key] = wanted
                self._pool.submit(pipewire.set_channel_eq, node_state.node_id, state)

    # ----- settings handlers ---------------------------------------------

    def _persist(self) -> None:
        save_settings(self.settings)

    def _on_autostart(self, value: bool) -> None:
        self.settings["autostart"] = value
        set_autostart(value)
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

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._eq_open:
                self._close_eq_panel()
            elif self._drawer_open:
                self._close_drawer()
            else:
                self.hide_panel()
            return
        super().keyPressEvent(event)

    # ----- audio state ----------------------------------------------------

    def _on_snapshot(self, snap: Snapshot) -> None:
        self._snapshot = snap
        self._ensure_eq_applied(snap)
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
        self._update_status(snap)

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
            pipewire.enforce_app_routes(snap, self._current_routes())
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
        """Assign an app to a category ("" = back to the mix) and move it.

        The move is a plain, gapless PipeWire retarget — EasyEffects is never
        restarted (its stream grabbing is disabled at setup), and the poller
        re-asserts the route should anything try to steal the stream back.
        """
        app = next((a for a in self._snapshot.apps if a.key == key), None)
        if app is None:
            return
        routes = self._current_routes()
        if channel_key:
            channel = next((c for c in ROUTABLE if c.key == channel_key), None)
            if channel is None:
                return
            routes[app.binary or app.key] = channel_key
            target = channel.node_name
        else:
            routes.pop(app.binary or app.key, None)
            target = self._snapshot.mix_target or self._snapshot.default_sink
        self.settings["routes"] = routes
        self._persist()
        self.app_mixer.mark_pending(key, channel_key)
        if target:
            self._pool.submit(self._route_worker, app, target)

    def _route_worker(self, app, sink: str) -> None:
        for serial in app.serials or [app.serial]:
            if serial:
                pipewire.move_stream(serial, sink)

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
        self._persist_eq()
        self.poller.stop()
        self._pool.shutdown(wait=False)
        super().closeEvent(event)
