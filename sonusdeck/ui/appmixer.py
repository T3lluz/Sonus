"""The routing block: category bins stacked on the left, master beside them.

Every app stream lives as a fader strip inside the MASTER container.
Dragging a strip (or a chip already inside a bin) onto a category bin assigns
the app to that category; dropping it back onto the master container
unassigns it. Bins mirror the assignment state with app-icon chips.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from .. import theme as T
from ..config import CHANNEL_BY_KEY, SINK_CHANNELS
from ..pipewire import AppStream
from .widgets import APP_METRICS, Strip, lerp

_icon_cache: dict[str, QIcon] = {}
_FALLBACKS = ("audio-x-generic", "multimedia-audio-player", "application-x-executable")

_PENDING_TTL = 6.0


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


class DragGhost(QWidget):
    """Chip following the cursor while an app is being dragged."""

    W = 168
    H = 38

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pixmap: QPixmap | None = None
        self._text = ""

    def set_content(self, text: str, pixmap: QPixmap | None) -> None:
        self._text = text
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(0.94)
        painter.setPen(QPen(QColor(T.RULE), 1))
        painter.setBrush(QBrush(QColor(T.MUTE_HOVER)))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.W - 1, self.H - 1), 12, 12)
        x = 10.0
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.drawPixmap(QRect(int(x), (self.H - 22) // 2, 22, 22), self._pixmap)
            x += 30
        painter.setFont(T.semibold(9))
        painter.setPen(QColor(T.TEXT))
        metrics = QFontMetrics(painter.font())
        text = metrics.elidedText(self._text, Qt.TextElideMode.ElideRight, int(self.W - x - 10))
        painter.drawText(
            QRectF(x, 0, self.W - x - 8, self.H),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text,
        )
        painter.end()


class CategoryBin(QWidget):
    """Drop container for one category, showing its assigned apps as chips."""

    chipDragMoved = pyqtSignal(str, QPoint)
    chipDragReleased = pyqtSignal(str, QPoint)

    def __init__(self, key: str, label: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.label = label
        self.accent = accent
        self.drop_hover = False
        self._apps: list[tuple[str, str, QPixmap | None]] = []
        self._chip_rects: list[QRectF] = []
        self._drag_key = ""
        self._maybe_drag = False
        self._dragging = False
        self._press_global = QPoint()
        self.setMouseTracking(True)

    # ----- state ---------------------------------------------------------

    def set_apps(self, apps: list[tuple[str, str, QPixmap | None]]) -> None:
        if [a[0] for a in apps] != [a[0] for a in self._apps]:
            self._apps = apps
            self._layout_chips()
            self.update()
        else:
            self._apps = apps

    def set_drop_hover(self, value: bool) -> None:
        if value != self.drop_hover:
            self.drop_hover = value
            self.update()

    def resizeEvent(self, event) -> None:
        self._layout_chips()

    def _layout_chips(self) -> None:
        self._chip_rects = []
        chip = T.BIN_CHIP
        gap = T.BIN_CHIP_GAP
        x0, y0 = 12.0, 40.0
        max_x = self.width() - 12
        x, y = x0, y0
        for _ in self._apps:
            if x + chip > max_x:
                x = x0
                y += chip + gap
            if y + chip > self.height() - 8:
                break
            self._chip_rects.append(QRectF(x, y, chip, chip))
            x += chip + gap

    # ----- input -----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        for rect, (key, _name, _pm) in zip(self._chip_rects, self._apps):
            if rect.contains(pos):
                self._drag_key = key
                self._maybe_drag = True
                self._dragging = False
                self._press_global = event.globalPosition().toPoint()
                return

    def mouseMoveEvent(self, event) -> None:
        if self._maybe_drag:
            global_pos = event.globalPosition().toPoint()
            if not self._dragging:
                delta = global_pos - self._press_global
                if abs(delta.x()) + abs(delta.y()) > 10:
                    self._dragging = True
            if self._dragging:
                self.chipDragMoved.emit(self._drag_key, global_pos)
            return

    def mouseReleaseEvent(self, event) -> None:
        if self._maybe_drag and self._dragging:
            self.chipDragReleased.emit(self._drag_key, event.globalPosition().toPoint())
        self._maybe_drag = False
        self._dragging = False
        self._drag_key = ""

    # ----- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        if self.drop_hover:
            fill = QColor(self.accent)
            fill.setAlpha(36)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(QColor(self.accent), 2))
        else:
            painter.setBrush(QBrush(QColor(T.PANEL)))
            painter.setPen(QPen(QColor(T.RULE), 1))
        painter.drawRoundedRect(rect, T.CARD_R - 4, T.CARD_R - 4)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(self.accent)))
        painter.drawEllipse(QPointF(19, 19), 5, 5)
        painter.setFont(T.semibold(10))
        painter.setPen(QColor(self.accent))
        painter.drawText(
            QRectF(32, 8, self.width() - 44, 22),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.label,
        )

        if not self._apps:
            painter.setFont(T.font(9))
            painter.setPen(QColor(T.DIM))
            painter.drawText(
                QRectF(12, 36, self.width() - 24, self.height() - 44),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
                | Qt.TextFlag.TextWordWrap.value,
                "Drop apps\nhere",
            )
        else:
            shown = len(self._chip_rects)
            for rect_, (_key, _name, pm) in zip(self._chip_rects, self._apps):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(T.MUTE_BG)))
                painter.drawRoundedRect(rect_, 8, 8)
                if pm is not None and not pm.isNull():
                    inner = rect_.adjusted(4, 4, -4, -4)
                    painter.drawPixmap(inner.toRect(), pm)
            extra = len(self._apps) - shown
            if extra > 0:
                painter.setFont(T.semibold(8))
                painter.setPen(QColor(T.DIM))
                painter.drawText(
                    QRectF(0, self.height() - 22, self.width() - 12, 16),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    f"+{extra}",
                )
        painter.end()


class MasterBox(QWidget):
    """The master container: every app's compact fader strip, scrollable."""

    volumeChanged = pyqtSignal(str, float)
    muteToggled = pyqtSignal(str)
    routeRequested = pyqtSignal(str, QPoint)
    dragMoved = pyqtSignal(str, QPoint)
    dragReleased = pyqtSignal(str, QPoint)

    STRIP_Y = T.MC_LABEL_H + 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.strips: dict[str, Strip] = {}
        self._order: list[str] = []
        self._scroll = 0.0
        self._scroll_to = 0.0
        self._bar_drag = False
        self._bar_hover = False
        self.drop_hover = False
        self.setFixedSize(T.APPS_VIEW_W - T.MC_X, T.MC_H)
        self.setMouseTracking(True)

        self.inner = QWidget(self)
        self.inner.setFixedHeight(APP_METRICS.height)
        self.inner.move(T.MC_PAD, self.STRIP_Y)
        self.inner.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.inner.setStyleSheet("background: transparent;")

    # ----- layout ------------------------------------------------------

    def set_view_width(self, width: int) -> None:
        self.setFixedSize(width, T.MC_H)
        self._clamp()
        self._apply_scroll()
        self.update()

    def set_drop_hover(self, value: bool) -> None:
        if value != self.drop_hover:
            self.drop_hover = value
            self.update()

    def _viewport_w(self) -> int:
        return self.width() - T.MC_PAD * 2

    def _content_w(self) -> int:
        count = len(self._order)
        if count <= 0:
            return 0
        return count * T.APP_STRIP_W + (count - 1) * T.STRIP_GAP

    def _max_scroll(self) -> float:
        return max(0.0, float(self._content_w() - self._viewport_w()))

    def _clamp(self) -> None:
        limit = self._max_scroll()
        self._scroll = min(self._scroll, limit)
        self._scroll_to = min(self._scroll_to, limit)

    def _apply_scroll(self) -> None:
        self.inner.move(T.MC_PAD - int(round(self._scroll)), self.STRIP_Y)

    def _bar_geometry(self) -> tuple[float, float, float]:
        content = self._content_w()
        track = self.width() - T.SCROLL_PAD * 2
        view = self._viewport_w()
        thumb = max(28.0, track * view / content) if content else track
        limit = self._max_scroll()
        x = T.SCROLL_PAD + (track - thumb) * (self._scroll / limit if limit else 0.0)
        return x, thumb, track

    # ----- data --------------------------------------------------------

    def apply(self, streams: list[AppStream], busy: set[str],
              accents: dict[str, str]) -> None:
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
                    width=T.APP_STRIP_W, app=True, metrics=APP_METRICS,
                    parent=self.inner,
                )
                strip.volumeChanged.connect(self.volumeChanged)
                strip.muteToggled.connect(self.muteToggled)
                strip.scrolled.connect(self._on_wheel_delta)
                strip.dragMoved.connect(self.dragMoved)
                strip.dragReleased.connect(self.dragReleased)
                strip.channelClicked.connect(
                    lambda k, s=strip: self.routeRequested.emit(
                        k, s.mapToGlobal(s.rect().center())
                    )
                )
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
            strip.set_accent(accents.get(stream.key, T.APP_ACCENT))
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
        return y >= self.height() - T.SCROLL_H - 4

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
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        if self.drop_hover:
            fill = QColor(T.APP_ACCENT)
            fill.setAlpha(22)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(QColor(T.TEXT), 2))
        else:
            painter.setBrush(QBrush(QColor(T.RULE)))
            painter.setPen(QPen(QColor(T.RULE), 1))
        painter.drawRoundedRect(rect, T.CARD_R - 4, T.CARD_R - 4)

        painter.setFont(T.semibold(10))
        painter.setPen(QColor(T.TEXT))
        painter.drawText(
            QRectF(T.MC_PAD + 4, 6, 120, T.MC_LABEL_H),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            "MASTER",
        )
        if self.width() >= 320:
            painter.setFont(T.font(9))
            painter.setPen(QColor(T.DIM))
            painter.drawText(
                QRectF(T.MC_PAD + 4 + 76, 6, self.width() - 120, T.MC_LABEL_H),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                "drag an app onto a category to assign it",
            )

        if not self._order:
            painter.setFont(T.semibold(11))
            painter.setPen(QColor(T.TEXT))
            painter.drawText(
                QRectF(0, self.height() / 2 - 26, self.width(), 22),
                Qt.AlignmentFlag.AlignCenter, "No app audio",
            )
            painter.setFont(T.font(9))
            painter.setPen(QColor(T.DIM))
            painter.drawText(
                QRectF(16, self.height() / 2 + 2, self.width() - 32, 40),
                Qt.AlignmentFlag.AlignCenter,
                "Apps that play sound appear here.",
            )
            painter.end()
            return

        if self._max_scroll() > 0:
            x, thumb, track = self._bar_geometry()
            mid = self.height() - T.SCROLL_H / 2 - 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(T.TRACK)))
            painter.drawRoundedRect(QRectF(T.SCROLL_PAD, mid - 2, track, 4), 2, 2)
            active = self._bar_hover or self._bar_drag
            painter.setBrush(QBrush(QColor(T.TEXT if active else T.DIM)))
            painter.drawRoundedRect(QRectF(x, mid - 3.5, thumb, 7), 3.5, 3.5)
        painter.end()


class AppMixer(QWidget):
    """Bins + master container, and the drag-and-drop between them."""

    volumeChanged = pyqtSignal(str, float)
    muteToggled = pyqtSignal(str)
    routeRequested = pyqtSignal(str, QPoint)
    assignRequested = pyqtSignal(str, str)  # app key, channel key ("" = master)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_w = T.APPS_VIEW_W
        self._streams: dict[str, AppStream] = {}
        self._pending: dict[str, tuple[str, float]] = {}
        self.setFixedSize(T.APPS_VIEW_W, T.STRIP_H)

        self.bins: dict[str, CategoryBin] = {}
        for channel in SINK_CHANNELS:
            bin_ = CategoryBin(channel.key, channel.label, channel.accent, self)
            bin_.chipDragMoved.connect(self._on_drag_moved)
            bin_.chipDragReleased.connect(self._on_drag_released)
            self.bins[channel.key] = bin_

        self.master = MasterBox(self)
        self.master.move(T.MC_X, 0)
        self.master.volumeChanged.connect(self.volumeChanged)
        self.master.muteToggled.connect(self.muteToggled)
        self.master.routeRequested.connect(self.routeRequested)
        self.master.dragMoved.connect(self._on_drag_moved)
        self.master.dragReleased.connect(self._on_drag_released)

        self._ghost: DragGhost | None = None
        self._layout_bins()

    @property
    def strips(self) -> dict[str, Strip]:
        return self.master.strips

    # ----- layout ------------------------------------------------------

    def set_view_width(self, width: int) -> None:
        width = max(T.APPS_VIEW_W, int(width))
        if width == self._view_w:
            return
        self._view_w = width
        self.setFixedSize(width, T.STRIP_H)
        self.master.set_view_width(width - T.MC_X)

    def _layout_bins(self) -> None:
        """Bins are a fixed-width column on the left, stacked vertically."""
        count = len(self.bins)
        bin_h = (T.STRIP_H - (count - 1) * T.BIN_GAP) / count
        y = 0.0
        for channel in SINK_CHANNELS:
            bin_ = self.bins[channel.key]
            top = int(round(y))
            bin_.setFixedSize(T.BIN_COL_W, int(round(y + bin_h)) - top)
            bin_.move(0, top)
            y += bin_h + T.BIN_GAP

    # ----- data --------------------------------------------------------

    def _effective_channel(self, stream: AppStream) -> str:
        pending = self._pending.get(stream.key)
        if pending is not None:
            channel, stamp = pending
            if stream.channel == channel or time.monotonic() - stamp > _PENDING_TTL:
                del self._pending[stream.key]
                return stream.channel
            return channel
        return stream.channel

    def mark_pending(self, key: str, channel: str) -> None:
        """Optimistic assignment shown until the graph snapshot confirms."""
        self._pending[key] = (channel, time.monotonic())
        self._refresh_bins()

    def apply(self, streams: list[AppStream], busy: set[str]) -> None:
        self._streams = {s.key: s for s in streams}
        accents: dict[str, str] = {}
        for stream in streams:
            channel = CHANNEL_BY_KEY.get(self._effective_channel(stream))
            accents[stream.key] = channel.accent if channel else T.APP_ACCENT
        self.master.apply(streams, busy, accents)
        self._refresh_bins()

    def _refresh_bins(self) -> None:
        grouped: dict[str, list[tuple[str, str, QPixmap | None]]] = {
            key: [] for key in self.bins
        }
        for stream in self._streams.values():
            channel = self._effective_channel(stream)
            if channel in grouped:
                pm = app_icon(stream).pixmap(20, 20)
                grouped[channel].append((stream.key, stream.name, pm))
        for key, bin_ in self.bins.items():
            bin_.set_apps(sorted(grouped[key], key=lambda item: item[1].lower()))

    def reset_display(self) -> None:
        self.master.reset_display()

    def tick(self, now: float) -> bool:
        return self.master.tick(now)

    # ----- drag and drop --------------------------------------------------

    def _bin_at(self, local: QPoint) -> CategoryBin | None:
        for bin_ in self.bins.values():
            if bin_.geometry().contains(local):
                return bin_
        return None

    def _on_drag_moved(self, key: str, global_pos: QPoint) -> None:
        local = self.mapFromGlobal(global_pos)
        if self._ghost is None:
            self._ghost = DragGhost(self)
            stream = self._streams.get(key)
            name = stream.name if stream else key
            pm = app_icon(stream).pixmap(22, 22) if stream else None
            self._ghost.set_content(name, pm)
            self._ghost.show()
        self._ghost.raise_()
        self._ghost.move(local + QPoint(14, -DragGhost.H // 2))

        target = self._bin_at(local)
        for bin_ in self.bins.values():
            bin_.set_drop_hover(bin_ is target)
        self.master.set_drop_hover(
            target is None and self.master.geometry().contains(local)
        )

    def _on_drag_released(self, key: str, global_pos: QPoint) -> None:
        local = self.mapFromGlobal(global_pos)
        target = self._bin_at(local)
        over_master = self.master.geometry().contains(local)
        self._end_drag()
        stream = self._streams.get(key)
        current = self._effective_channel(stream) if stream else ""
        if target is not None and target.key != current:
            self.mark_pending(key, target.key)
            self.assignRequested.emit(key, target.key)
        elif target is None and over_master and current:
            self.mark_pending(key, "")
            self.assignRequested.emit(key, "")

    def _end_drag(self) -> None:
        if self._ghost is not None:
            self._ghost.deleteLater()
            self._ghost = None
        for bin_ in self.bins.values():
            bin_.set_drop_hover(False)
        self.master.set_drop_hover(False)
