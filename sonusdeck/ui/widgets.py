"""Custom painted controls: faders, toggles, chips and buttons."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import QWidget

from .. import theme as T
from . import icons


def lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k


def _no_pen(painter: QPainter) -> None:
    painter.setPen(Qt.PenStyle.NoPen)


def draw_eq_glyph(painter: QPainter, rect: QRectF, color: str) -> None:
    """Three mini-faders, the equaliser affordance."""
    pen = QPen(QColor(color), 1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    pad_x = rect.width() * 0.26
    top = rect.top() + rect.height() * 0.24
    bottom = rect.bottom() - rect.height() * 0.24
    knots = (0.34, 0.72, 0.48)
    for i in range(3):
        x = rect.left() + pad_x + i * (rect.width() - pad_x * 2) / 2
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
    _no_pen(painter)
    painter.setBrush(QBrush(QColor(color)))
    for i, k in enumerate(knots):
        x = rect.left() + pad_x + i * (rect.width() - pad_x * 2) / 2
        y = top + (bottom - top) * k
        painter.drawEllipse(QPointF(x, y), 2.6, 2.6)


@dataclass(frozen=True)
class StripMetrics:
    """Vertical layout of a fader strip. Defaults match the channel strips."""

    height: int = T.STRIP_H
    icon_size: int = T.ICON_SIZE
    icon_y: int = T.ICON_Y
    label_y: int = T.LABEL_Y
    pct_y: int = T.PCT_Y
    fader_top: int = T.FADER_TOP
    fader_bot: int = T.FADER_BOT
    mute_size: int = T.MUTE_SIZE
    mute_y: int = T.MUTE_Y0
    thumb_w: float = T.THUMB_W
    thumb_h: float = T.THUMB_H
    track_w: float = T.TRACK_W
    label_pt: int = 10
    pct_pt: int = 9


CHANNEL_METRICS = StripMetrics()

# App strips inside the master container: nearly full height, long faders.
APP_METRICS = StripMetrics(
    height=476, icon_size=38, icon_y=36, label_y=74, pct_y=94,
    fader_top=130, fader_bot=400, mute_size=38, mute_y=424,
    thumb_w=20.0, thumb_h=36.0, track_w=9.0, label_pt=10, pct_pt=9,
)


class Strip(QWidget):
    """One vertical fader with icon, label, percentage and mute button.

    App strips (compact, inside the master container) can additionally be
    picked up and dragged onto a category bin: dragMoved/dragReleased carry
    global cursor positions for the drop handling in the mixer.
    """

    volumeChanged = pyqtSignal(str, float)
    muteToggled = pyqtSignal(str)
    scrolled = pyqtSignal(int)
    channelClicked = pyqtSignal(str)
    eqClicked = pyqtSignal(str)
    dragMoved = pyqtSignal(str, QPoint)
    dragReleased = pyqtSignal(str, QPoint)

    def __init__(
        self,
        key: str,
        label: str,
        accent: str,
        *,
        width: int = T.STRIP_W,
        app: bool = False,
        icon_key: str = "",
        eq_icon: bool = False,
        metrics: StripMetrics | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.label = label
        self.accent = accent
        self.app = app
        self.icon_key = icon_key or key
        self.strip_w = width
        self.m = metrics or (APP_METRICS if app else CHANNEL_METRICS)
        self.setFixedSize(width, self.m.height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.volume = 1.0
        self.target = 1.0
        self.display = 1.0
        self.muted = False
        self.dragging = False
        self.interact = False
        self._interact_until = 0.0
        self._mute_hold_until = 0.0
        self._mute_hover = False
        self._thumb_w = self.m.thumb_w
        self._thumb_h = self.m.thumb_h
        self._pixmap: QPixmap | None = None
        self._allow_scroll_through = app
        self.fader_top = float(self.m.fader_top)
        self.fader_bot = float(self.m.fader_bot)

        self._maybe_drag = False
        self._app_drag = False
        self._press_global = QPoint()

        self.eq_icon = eq_icon
        self.eq_active = False
        self._eq_hover = False
        self._eq_rect = QRectF(
            width - T.EQ_ICON_SIZE - T.EQ_ICON_PAD, T.EQ_ICON_PAD,
            T.EQ_ICON_SIZE, T.EQ_ICON_SIZE,
        )

        mx = (width - self.m.mute_size) / 2
        self._mute_rect = QRectF(mx, self.m.mute_y, self.m.mute_size, self.m.mute_size)

    # ----- state -------------------------------------------------------

    def apply_state(self, volume: float, muted: bool) -> None:
        if not self.dragging:
            self.volume = max(0.0, min(1.0, volume))
        if muted != self.muted:
            # A snapshot taken just before a local mute landed still carries
            # the old flag; honouring it would bounce the bar. Hold the local
            # state until the poller has had time to observe the change.
            if time.perf_counter() >= self._mute_hold_until:
                self.muted = muted
        if not self.dragging:
            self.target = 0.0 if self.muted else self.volume
        self.update()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self._mute_hold_until = time.perf_counter() + 1.5
        self.target = 0.0 if muted else self.volume
        self.update()

    def set_meta(self, label: str, icon: QIcon | None) -> None:
        changed = label != self.label
        self.label = label
        if icon is not None and not icon.isNull():
            pm = icon.pixmap(T.APP_ICON, T.APP_ICON)
            if not pm.isNull():
                self._pixmap = pm
                changed = True
        if changed:
            self.update()

    def set_accent(self, accent: str) -> None:
        if accent != self.accent:
            self.accent = accent
            self.update()

    def set_eq_active(self, active: bool) -> None:
        if active != self.eq_active:
            self.eq_active = active
            self.update()

    def reset_display(self) -> None:
        self.display = 0.0
        self.interact = False
        self.update()

    # ----- animation ---------------------------------------------------

    def tick(self, now: float) -> bool:
        if self.interact and not self.dragging and self._interact_until and now >= self._interact_until:
            self.interact = False
            self._interact_until = 0.0
        moving = abs(self.target - self.display) > 0.002
        if moving:
            self.display = lerp(self.display, self.target, 0.15)
        else:
            self.display = self.target
        live = self.dragging or self.interact
        want_w = self.m.thumb_w + (2.0 if live else 0.0)
        want_h = self.m.thumb_h + (4.0 if live else 0.0)
        self._thumb_w += (want_w - self._thumb_w) * 0.32
        self._thumb_h += (want_h - self._thumb_h) * 0.32
        if moving or live or abs(self._thumb_w - want_w) > 0.04:
            self.update()
            return True
        return False

    # ----- input -------------------------------------------------------

    def _value_at(self, y: float) -> float:
        span = max(1.0, self.fader_bot - self.fader_top)
        return max(0.0, min(1.0, 1.0 - (y - self.fader_top) / span))

    def _apply_from_pointer(self, y: float) -> None:
        self.volume = self._value_at(y)
        self.target = self.volume
        self.display = self.volume
        self.volumeChanged.emit(self.key, self.volume)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if self._mute_rect.contains(pos):
            self.muteToggled.emit(self.key)
            return
        if self.eq_icon and self._eq_rect.contains(pos):
            self.eqClicked.emit(self.key)
            return
        if self.app and pos.y() < self.fader_top - 6:
            # Header grab: becomes a drag onto a category bin, or a click.
            self._maybe_drag = True
            self._app_drag = False
            self._press_global = event.globalPosition().toPoint()
            return
        if self.muted:
            self.muteToggled.emit(self.key)
        self.dragging = True
        self.interact = True
        self._apply_from_pointer(pos.y())

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._maybe_drag:
            global_pos = event.globalPosition().toPoint()
            if not self._app_drag:
                delta = global_pos - self._press_global
                if abs(delta.x()) + abs(delta.y()) > 10:
                    self._app_drag = True
            if self._app_drag:
                self.dragMoved.emit(self.key, global_pos)
            return
        if self.dragging:
            self._apply_from_pointer(pos.y())
            return
        repaint = False
        hover = self._mute_rect.contains(pos)
        if hover != self._mute_hover:
            self._mute_hover = hover
            repaint = True
        eq_hover = self.eq_icon and self._eq_rect.contains(pos)
        if eq_hover != self._eq_hover:
            self._eq_hover = eq_hover
            repaint = True
        if repaint:
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._maybe_drag:
            if self._app_drag:
                self.dragReleased.emit(self.key, event.globalPosition().toPoint())
            else:
                self.channelClicked.emit(self.key)
            self._maybe_drag = False
            self._app_drag = False
            return
        if self.dragging:
            self.dragging = False
            self.interact = False
            self.volumeChanged.emit(self.key, self.volume)

    def leaveEvent(self, event) -> None:
        if self._mute_hover or self._eq_hover:
            self._mute_hover = False
            self._eq_hover = False
            self.update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        inside_fader = self.fader_top <= event.position().y() <= self.fader_bot
        if self._allow_scroll_through and not inside_fader:
            self.scrolled.emit(delta)
            event.accept()
            return
        if self.muted:
            self.muteToggled.emit(self.key)
        self.interact = True
        self._interact_until = time.perf_counter() + 0.28
        step = 0.04 if delta > 0 else -0.04
        self.volume = max(0.0, min(1.0, self.volume + step))
        self.target = self.volume
        self.display = self.volume
        self.volumeChanged.emit(self.key, self.volume)
        self.update()
        event.accept()

    # ----- painting ----------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        _no_pen(painter)

        m = self.m
        painter.setBrush(QBrush(QColor(T.PANEL)))
        painter.drawRoundedRect(QRectF(0, 0, self.strip_w, m.height), T.CARD_R, T.CARD_R)

        cx = self.strip_w / 2
        head = T.DIM if self.muted else (T.TEXT if self.app else self.accent)

        icon_rect = QRectF(cx - m.icon_size / 2, m.icon_y - m.icon_size / 2, m.icon_size, m.icon_size)
        if self._pixmap is not None:
            painter.setOpacity(0.45 if self.muted else 1.0)
            painter.drawPixmap(icon_rect.toRect(), self._pixmap)
            painter.setOpacity(1.0)
        else:
            icons.draw_channel(painter, self.icon_key, icon_rect, head)

        painter.setFont(T.semibold(m.label_pt))
        metrics = QFontMetrics(painter.font())
        text = metrics.elidedText(self.label, Qt.TextElideMode.ElideRight, self.strip_w - 24)
        painter.setPen(QColor(head))
        painter.drawText(
            QRectF(0, m.label_y - 12, self.strip_w, 24),
            Qt.AlignmentFlag.AlignCenter, text,
        )

        painter.setFont(T.font(m.pct_pt))
        painter.setPen(QColor(T.DIM))
        painter.drawText(
            QRectF(0, m.pct_y - 11, self.strip_w, 22),
            Qt.AlignmentFlag.AlignCenter, f"{int(round(self.display * 100))}%",
        )

        if self.eq_icon:
            self._paint_eq_icon(painter)

        fill_color = T.FILL_MUTED if self.muted else self.accent
        cy = self.fader_bot - (self.fader_bot - self.fader_top) * self.display

        pen = QPen(QColor(T.TRACK), m.track_w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx, self.fader_top), QPointF(cx, self.fader_bot))

        if self.fader_bot - cy > 0.5:
            pen = QPen(QColor(fill_color), m.track_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx, cy), QPointF(cx, self.fader_bot))

        _no_pen(painter)
        tw, th = self._thumb_w, self._thumb_h
        painter.setBrush(QBrush(QColor(T.THUMB)))
        painter.drawRoundedRect(
            QRectF(cx - tw / 2, cy - th / 2, tw, th), min(tw, th) / 2, min(tw, th) / 2
        )
        pen = QPen(QColor(fill_color), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(cx - 4.5, cy), QPointF(cx + 4.5, cy))

        _no_pen(painter)
        painter.setBrush(QBrush(QColor(T.MUTE_HOVER if self._mute_hover else T.MUTE_BG)))
        painter.drawEllipse(self._mute_rect)
        if self.muted:
            glyph = T.MUTE_FG_HOVER if self._mute_hover else T.MUTE_FG
        else:
            glyph = T.TEXT
        size = self.m.mute_size * 0.52
        icons.draw_speaker(
            painter,
            QRectF(
                self._mute_rect.x() + (self.m.mute_size - size) / 2,
                self._mute_rect.y() + (self.m.mute_size - size) / 2,
                size, size,
            ),
            glyph, self.muted,
        )
        painter.end()

    def _paint_eq_icon(self, painter: QPainter) -> None:
        """Small mini-fader glyph opening the category equaliser."""
        rect = self._eq_rect
        _no_pen(painter)
        if self._eq_hover:
            painter.setBrush(QBrush(QColor(T.MUTE_HOVER)))
            painter.drawRoundedRect(rect, 7, 7)
        elif self.eq_active:
            painter.setBrush(QBrush(QColor(T.MUTE_BG)))
            painter.drawRoundedRect(rect, 7, 7)
        color = self.accent if (self.eq_active or self._eq_hover) else T.DIM
        draw_eq_glyph(painter, rect, color)


class Toggle(QWidget):
    """Pill switch used in the settings drawer."""

    toggled = pyqtSignal(bool)

    W = 48
    H = 26
    INSET = 3

    def __init__(self, value: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = bool(value)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, value: bool) -> None:
        self.value = bool(value)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.value = not self.value
        self.update()
        self.toggled.emit(self.value)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _no_pen(painter)
        painter.setBrush(QBrush(QColor(T.FILL if self.value else T.SWITCH_OFF)))
        rect = QRectF(1, 1, self.W - 2, self.H - 2)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        radius = (rect.height() - self.INSET * 2) / 2
        cy = self.H / 2
        cx = (rect.right() - self.INSET - radius) if self.value else (rect.left() + self.INSET + radius)
        painter.setBrush(QBrush(QColor(T.THUMB)))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.end()


class IconButton(QWidget):
    """Small square button drawing either a gear or a cross."""

    clicked = pyqtSignal()

    def __init__(self, kind: str, size: int = 32, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = self.width()
        danger = self.kind == "close"
        if self._hover:
            _no_pen(painter)
            painter.setBrush(QBrush(QColor(T.CLOSE_HOVER if danger else T.MUTE_BG)))
            painter.drawRoundedRect(QRectF(0, 0, size, size), 9, 9)
        if danger:
            color = T.CLOSE_FG_HOVER if self._hover else T.DIM
        else:
            color = T.TEXT if self._hover else T.DIM

        if self.kind == "gear":
            self._draw_gear(painter, size, color)
        else:
            pen = QPen(QColor(color), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            pad = 10
            painter.drawLine(QPointF(pad, pad), QPointF(size - pad, size - pad))
            painter.drawLine(QPointF(size - pad, pad), QPointF(pad, size - pad))
        painter.end()

    @staticmethod
    def _draw_gear(painter: QPainter, size: int, color: str) -> None:
        cx = cy = size / 2
        outer = size * 0.30
        tooth_w = size * 0.115
        tooth_h = size * 0.155
        _no_pen(painter)
        painter.setBrush(QBrush(QColor(color)))
        painter.save()
        painter.translate(cx, cy)
        for i in range(8):
            painter.save()
            painter.rotate(i * 45.0)
            painter.drawRoundedRect(
                QRectF(-tooth_w / 2, -(outer + tooth_h * 0.55), tooth_w, tooth_h),
                tooth_w * 0.35, tooth_w * 0.35,
            )
            painter.restore()
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), outer, outer)
        inner = QPainterPath()
        inner.addEllipse(QPointF(0, 0), outer * 0.44, outer * 0.44)
        painter.drawPath(path.subtracted(inner))
        painter.restore()


class ChipButton(QWidget):
    """Rounded pill button with a label and an optional leading dot."""

    clicked = pyqtSignal()

    def __init__(
        self,
        text: str,
        *,
        height: int = 32,
        mark: bool = False,
        surface: str = T.MUTE_BG,
        hover: str = T.MUTE_HOVER,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._mark = mark
        self._surface = surface
        self._hover_color = hover
        self._hover = False
        self._accent = T.FILL
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._relayout()

    def _relayout(self) -> None:
        metrics = QFontMetrics(T.semibold(10))
        width = 28 + metrics.horizontalAdvance(self._text)
        if self._mark:
            width += 24
        self.setFixedWidth(max(72, width))

    def set_text(self, text: str, accent: str | None = None) -> None:
        self._text = text
        if accent:
            self._accent = accent
        self._relayout()
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _no_pen(painter)
        h = self.height()
        painter.setBrush(QBrush(QColor(self._hover_color if self._hover else self._surface)))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), h), h / 2, h / 2)
        x = 14.0
        if self._mark:
            radius = 5.0
            painter.setBrush(QBrush(QColor(self._accent)))
            painter.drawEllipse(QPointF(x + radius, h / 2), radius, radius)
            x += radius * 2 + 8
        painter.setFont(T.semibold(10))
        painter.setPen(QColor(T.TEXT))
        painter.drawText(
            QRectF(x, 0, self.width() - x - 12, h),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._text,
        )
        painter.end()


class Card(QWidget):
    """Rounded surface used behind settings rows."""

    def __init__(self, radius: int = T.SETTINGS_CARD_R, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._radius = radius

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _no_pen(painter)
        painter.setBrush(QBrush(QColor(T.MUTE_BG)))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), self._radius, self._radius)
        painter.end()


class BrandMark(QWidget):
    """Sonus mark: a sonar pulse."""

    def __init__(self, size: int = 26, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = self.width()
        cx = cy = size / 2
        painter.setBrush(QBrush(QColor(T.FILL)))
        _no_pen(painter)
        painter.drawEllipse(QPointF(cx, cy), size * 0.14, size * 0.14)
        for i, radius in enumerate((0.28, 0.42)):
            pen = QPen(QColor(T.FILL), size * 0.085)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(cx - size * radius, cy - size * radius, size * radius * 2, size * radius * 2)
            painter.drawArc(rect, int(-55 * 16), int(110 * 16))
            painter.drawArc(rect, int(125 * 16), int(110 * 16))
        painter.end()


class AppsMark(QWidget):
    """Four rounded panes standing in for the app column."""

    PANES = ("#35B697", "#539FEA", "#E562AE", "#F8A056")

    def __init__(self, size: int = 26, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _no_pen(painter)
        size = self.width()
        gap = size * 0.10
        cell = (size - gap) / 2
        radius = cell * 0.26
        spots = ((0, 0), (cell + gap, 0), (0, cell + gap), (cell + gap, cell + gap))
        for (x, y), color in zip(spots, self.PANES):
            painter.setBrush(QBrush(QColor(color)))
            painter.drawRoundedRect(QRectF(x, y, cell, cell), radius, radius)
        painter.end()
