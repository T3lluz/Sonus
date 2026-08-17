"""Horizontally scrolling column of per-application faders."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter
from PyQt6.QtWidgets import QWidget

from .. import theme as T
from ..pipewire import AppStream
from .widgets import Strip, lerp

_icon_cache: dict[str, QIcon] = {}
_FALLBACKS = ("audio-x-generic", "multimedia-audio-player", "application-x-executable")


def app_icon(stream: AppStream) -> QIcon:
    cached = _icon_cache.get(stream.key)
    if cached is not None:
        return cached
    candidates = [stream.icon_name, stream.binary, stream.name.lower().replace(" ", "-")]
    icon = QIcon()
    for name in candidates:
        if not name:
            continue
        found = QIcon.fromTheme(name)
        if not found.isNull():
            icon = found
            break
    if icon.isNull():
        for name in _FALLBACKS:
            found = QIcon.fromTheme(name)
            if not found.isNull():
                icon = found
                break
    _icon_cache[stream.key] = icon
    return icon


class AppMixer(QWidget):
    volumeChanged = pyqtSignal(str, float)
    muteToggled = pyqtSignal(str)
    routeRequested = pyqtSignal(str, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.strips: dict[str, Strip] = {}
        self._order: list[str] = []
        self._view_w = T.APPS_VIEW_W
        self._scroll = 0.0
        self._scroll_to = 0.0
        self._bar_drag = False
        self._bar_hover = False
        self.setFixedSize(T.APPS_VIEW_W, T.STRIP_H)
        self.setMouseTracking(True)

        self.inner = QWidget(self)
        self.inner.setFixedHeight(T.STRIP_H)
        self.inner.move(0, 0)
        self.inner.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.inner.setStyleSheet("background: transparent;")

    # ----- layout ------------------------------------------------------

    def set_view_width(self, width: int) -> None:
        width = max(T.APPS_VIEW_W, int(width))
        if width == self._view_w:
            return
        self._view_w = width
        self.setFixedWidth(width)
        self._clamp()
        self._apply_scroll()
        self.update()

    def _content_w(self) -> int:
        count = len(self._order)
        if count <= 0:
            return 0
        return count * T.APP_STRIP_W + (count - 1) * T.STRIP_GAP

    def _max_scroll(self) -> float:
        return max(0.0, float(self._content_w() - self._view_w))

    def _clamp(self) -> None:
        limit = self._max_scroll()
        self._scroll = min(self._scroll, limit)
        self._scroll_to = min(self._scroll_to, limit)

    def _apply_scroll(self) -> None:
        self.inner.move(int(-round(self._scroll)), 0)

    def _bar_geometry(self) -> tuple[float, float, float]:
        content = self._content_w()
        track = self._view_w - T.SCROLL_PAD * 2
        thumb = max(28.0, track * self._view_w / content) if content else track
        limit = self._max_scroll()
        x = T.SCROLL_PAD + (track - thumb) * (self._scroll / limit if limit else 0.0)
        return x, thumb, track

    # ----- data --------------------------------------------------------

    def apply(self, streams: list[AppStream], busy: set[str]) -> None:
        incoming = {s.key: s for s in streams}
        self._order = [s.key for s in streams]

        for key in list(self.strips):
            if key not in incoming:
                self.strips.pop(key).deleteLater()

        for index, stream in enumerate(streams):
            strip = self.strips.get(stream.key)
            if strip is None:
                strip = Strip(
                    stream.key, stream.name, T.APP_ACCENT,
                    width=T.APP_STRIP_W, app=True, parent=self.inner,
                )
                strip.volumeChanged.connect(self.volumeChanged)
                strip.muteToggled.connect(self.muteToggled)
                strip.scrolled.connect(self._on_wheel_delta)
                strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                strip.customContextMenuRequested.connect(
                    lambda pos, k=stream.key, s=strip: self.routeRequested.emit(
                        k, s.mapToGlobal(pos)
                    )
                )
                strip.set_meta(stream.name, app_icon(stream))
                strip.apply_state(stream.volume, stream.muted)
                strip.show()
                self.strips[stream.key] = strip
            else:
                strip.set_meta(stream.name, app_icon(stream))
                if stream.key not in busy:
                    strip.apply_state(stream.volume, stream.muted)
            strip.move(index * (T.APP_STRIP_W + T.STRIP_GAP), 0)

        self.inner.setFixedWidth(max(1, self._content_w()))
        self._clamp()
        self._apply_scroll()
        self.update()

    def reset_display(self) -> None:
        for strip in self.strips.values():
            strip.reset_display()

    def tick(self, now: float) -> bool:
        busy = False
        if not self._bar_drag and abs(self._scroll - self._scroll_to) > 0.4:
            self._scroll = lerp(self._scroll, self._scroll_to, 0.24)
            if abs(self._scroll - self._scroll_to) <= 0.4:
                self._scroll = self._scroll_to
            self._apply_scroll()
            self.update()
            busy = True
        for strip in self.strips.values():
            busy = strip.tick(now) or busy
        return busy

    # ----- input -------------------------------------------------------

    def _on_wheel_delta(self, delta: int) -> None:
        limit = self._max_scroll()
        if limit <= 0:
            return
        self._scroll_to = max(0.0, min(limit, self._scroll_to - delta / 120.0 * 48.0))

    def wheelEvent(self, event) -> None:
        self._on_wheel_delta(event.angleDelta().y())
        event.accept()

    def _scroll_from_x(self, x: float) -> None:
        limit = self._max_scroll()
        if limit <= 0:
            return
        _, thumb, track = self._bar_geometry()
        ratio = (x - T.SCROLL_PAD - thumb / 2) / max(1.0, track - thumb)
        value = max(0.0, min(limit, ratio * limit))
        self._scroll = value
        self._scroll_to = value
        self._apply_scroll()
        self.update()

    def _in_bar(self, y: float) -> bool:
        return y >= T.STRIP_H - T.SCROLL_H - 4

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._max_scroll() > 0 and self._in_bar(event.position().y()):
            self._bar_drag = True
            self._scroll_from_x(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._bar_drag:
            self._scroll_from_x(event.position().x())
            return
        hover = self._in_bar(event.position().y())
        if hover != self._bar_hover:
            self._bar_hover = hover
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._bar_drag = False

    def leaveEvent(self, event) -> None:
        if self._bar_hover:
            self._bar_hover = False
            self.update()

    # ----- painting ----------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        if not self._order:
            painter.setBrush(QBrush(QColor(T.PANEL)))
            painter.drawRoundedRect(
                QRectF(0, 0, self._view_w, T.STRIP_H), T.CARD_R, T.CARD_R
            )
            painter.setFont(T.semibold(12))
            painter.setPen(QColor(T.TEXT))
            painter.drawText(
                QRectF(0, T.STRIP_H / 2 - 30, self._view_w, 24),
                Qt.AlignmentFlag.AlignCenter, "APPS",
            )
            painter.setFont(T.font(10))
            painter.setPen(QColor(T.DIM))
            painter.drawText(
                QRectF(0, T.STRIP_H / 2 + 2, self._view_w, 22),
                Qt.AlignmentFlag.AlignCenter, "No app audio",
            )
            painter.end()
            return

        if self._max_scroll() > 0:
            x, thumb, track = self._bar_geometry()
            mid = T.STRIP_H - T.SCROLL_H / 2
            painter.setBrush(QBrush(QColor(T.TRACK)))
            painter.drawRoundedRect(
                QRectF(T.SCROLL_PAD, mid - 2, track, 4), 2, 2
            )
            active = self._bar_hover or self._bar_drag
            painter.setBrush(QBrush(QColor(T.TEXT if active else T.DIM)))
            painter.drawRoundedRect(QRectF(x, mid - 3.5, thumb, 7), 3.5, 3.5)
        painter.end()
