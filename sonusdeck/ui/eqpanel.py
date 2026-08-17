"""Full-page per-category equaliser, in the spirit of SteelSeries Sonar.

One page is reused for every category: Panel slides it over the deck, points
it at a channel with set_channel(), and listens for eqChanged to push values
into the PipeWire filter-chain and the EasyEffects preset mirror. The page
resizes with the deck via set_page_width().
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QWidget

from .. import eq as eqmod
from .. import theme as T
from .widgets import ChipButton, Toggle

_M = T.EQ_MARGIN
_VALUE_Y = 66
_SLIDER_Y = 88
_FREQ_Y = _SLIDER_Y + T.EQ_SLIDER_H + 10
_PRESETS_Y = 96
_PREAMP_Y = 330
_NOTE_Y = 408


class EqSlider(QWidget):
    """One dB fader, vertical for bands or horizontal for the preamp.

    `value` is the target; `display` is what gets painted and eases toward
    the target every frame (see tick), so presets, resets and the page
    entrance all glide instead of snapping. Dragging writes both directly.
    """

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        accent: str,
        *,
        horizontal: bool = False,
        limit: float = eqmod.GAIN_LIMIT,
        length: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.accent = accent
        self.horizontal = horizontal
        self.limit = limit
        self.value = 0.0
        self.display = 0.0
        self._dragging = False
        if horizontal:
            self.setFixedSize(length or 240, 26)
        else:
            self.setFixedSize(34, length or T.EQ_SLIDER_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ----- state ---------------------------------------------------------

    def set_value(self, value: float) -> None:
        value = eqmod.clamp_gain(value, self.limit)
        if abs(value - self.value) > 0.001:
            self.value = value
            self.update()

    def reset_display(self) -> None:
        """Park the painted position on the 0 dB detent for the entrance."""
        self.display = 0.0
        self.update()

    # ----- animation -------------------------------------------------------

    def tick(self) -> bool:
        """Ease the painted position toward the target. True while moving."""
        if self._dragging:
            return False
        delta = self.value - self.display
        if abs(delta) > 0.02:
            self.display += delta * 0.09
            self.update()
            return True
        if self.display != self.value:
            self.display = self.value
            self.update()
        return False

    def set_accent(self, accent: str) -> None:
        if accent != self.accent:
            self.accent = accent
            self.update()

    # ----- geometry --------------------------------------------------------

    def _span(self) -> tuple[float, float]:
        if self.horizontal:
            return 8.0, self.width() - 8.0
        return 8.0, self.height() - 8.0

    def _pos_for_value(self) -> float:
        lo, hi = self._span()
        shown = max(-self.limit, min(self.limit, self.display))
        frac = (shown + self.limit) / (2 * self.limit)
        if self.horizontal:
            return lo + (hi - lo) * frac
        return hi - (hi - lo) * frac

    def _value_at(self, coord: float) -> float:
        lo, hi = self._span()
        span = max(1.0, hi - lo)
        frac = (coord - lo) / span if self.horizontal else 1.0 - (coord - lo) / span
        raw = (max(0.0, min(1.0, frac)) * 2.0 - 1.0) * self.limit
        # Snap near the 0 dB detent so "flat" is easy to hit.
        return 0.0 if abs(raw) < 0.35 else round(raw * 2) / 2

    # ----- input -------------------------------------------------------------

    def _apply(self, pos: QPointF) -> None:
        coord = pos.x() if self.horizontal else pos.y()
        value = self._value_at(coord)
        if abs(value - self.value) > 0.001:
            self.value = value
            self.display = value
            self.update()
            self.valueChanged.emit(value)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self._apply(event.position())

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._apply(event.position())

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False

    def mouseDoubleClickEvent(self, event) -> None:
        if self.value != 0.0:
            self.value = 0.0
            self.update()
            self.valueChanged.emit(0.0)

    def wheelEvent(self, event) -> None:
        step = 0.5 if event.angleDelta().y() > 0 else -0.5
        value = eqmod.clamp_gain(self.value + step, self.limit)
        if value != self.value:
            self.value = value
            self.update()
            self.valueChanged.emit(value)
        event.accept()

    # ----- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        lo, hi = self._span()
        centre = (lo + hi) / 2
        pos = self._pos_for_value()
        track = QPen(QColor(T.TRACK), T.EQ_TRACK_W)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        fill = QPen(QColor(self.accent), T.EQ_TRACK_W)
        fill.setCapStyle(Qt.PenCapStyle.RoundCap)

        if self.horizontal:
            mid = self.height() / 2
            painter.setPen(track)
            painter.drawLine(QPointF(lo, mid), QPointF(hi, mid))
            painter.setPen(fill)
            if abs(pos - centre) > 0.5:
                painter.drawLine(QPointF(centre, mid), QPointF(pos, mid))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(T.THUMB)))
            painter.drawRoundedRect(
                QRectF(pos - T.EQ_THUMB_H / 2, mid - T.EQ_THUMB_W / 2,
                       T.EQ_THUMB_H, T.EQ_THUMB_W), 5, 5,
            )
        else:
            mid = self.width() / 2
            painter.setPen(track)
            painter.drawLine(QPointF(mid, lo), QPointF(mid, hi))
            painter.setPen(fill)
            if abs(pos - centre) > 0.5:
                painter.drawLine(QPointF(mid, centre), QPointF(mid, pos))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(T.THUMB)))
            painter.drawRoundedRect(
                QRectF(mid - T.EQ_THUMB_W / 2, pos - T.EQ_THUMB_H / 2,
                       T.EQ_THUMB_W, T.EQ_THUMB_H), 5, 5,
            )
        painter.end()


class EqPanel(QWidget):
    """Full-page equaliser: presets and preamp on the left, bands spread wide."""

    eqChanged = pyqtSignal(str)
    backRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(T.PAGE_W, T.STRIP_H)
        self.channel_key = ""
        self.accent = T.FILL
        self._loading = False

        self.back_chip = ChipButton("\u2190  Back", parent=self)
        self.back_chip.move(_M, 12)
        self.back_chip.clicked.connect(self.backRequested)

        self.title = QLabel("EQUALIZER", self)
        self.title.setFont(T.semibold(15))
        self.title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.title.setFixedSize(360, 32)
        self.title.move(_M + self.back_chip.width() + 40, 12)

        self.enabled_label = QLabel("EQ enabled", self)
        self.enabled_label.setFont(T.semibold(10))
        self.enabled_label.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.enabled_label.adjustSize()
        self.toggle = Toggle(True, self)
        self.toggle.toggled.connect(self._on_any_change)
        self.reset_chip = ChipButton("Reset", parent=self)
        self.reset_chip.clicked.connect(self._reset)

        presets_caption = QLabel("PRESETS", self)
        presets_caption.setFont(T.semibold(9))
        presets_caption.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        presets_caption.move(_M, _PRESETS_Y - 24)

        y = _PRESETS_Y
        self.preset_chips: list[ChipButton] = []
        for name in eqmod.PRESETS:
            chip = ChipButton(name, height=30, parent=self)
            chip.setFixedWidth(T.EQ_LEFT_COL - 60)
            chip.move(_M, y)
            chip.clicked.connect(lambda n=name: self._apply_preset(n))
            self.preset_chips.append(chip)
            y += 30 + 8

        preamp_caption = QLabel("PREAMP", self)
        preamp_caption.setFont(T.semibold(9))
        preamp_caption.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        preamp_caption.move(_M, _PREAMP_Y - 24)
        self.preamp = EqSlider(
            self.accent, horizontal=True, limit=eqmod.PREAMP_LIMIT,
            length=T.EQ_LEFT_COL - 90, parent=self,
        )
        self.preamp.move(_M, _PREAMP_Y)
        self.preamp.valueChanged.connect(lambda _v: self._on_any_change())
        self.preamp_value = QLabel("0.0 dB", self)
        self.preamp_value.setFont(T.font(9))
        self.preamp_value.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        self.preamp_value.setFixedWidth(70)
        self.preamp_value.move(_M + self.preamp.width() + 10, _PREAMP_Y + 5)

        self.note = QLabel("", self)
        self.note.setFont(T.font(9))
        self.note.setStyleSheet(f"color: {T.DIM}; background: transparent;")
        self.note.setWordWrap(True)
        self.note.setFixedWidth(T.EQ_LEFT_COL)
        self.note.move(_M, _NOTE_Y)

        self.sliders: list[EqSlider] = []
        self._value_labels: list[QLabel] = []
        self._freq_labels: list[QLabel] = []
        for index in range(eqmod.BAND_COUNT):
            slider = EqSlider(self.accent, parent=self)
            slider.valueChanged.connect(lambda _v, i=index: self._on_band(i))
            self.sliders.append(slider)

            value = QLabel("0", self)
            value.setFont(T.font(9))
            value.setStyleSheet(f"color: {T.DIM}; background: transparent;")
            value.setFixedHeight(16)
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._value_labels.append(value)

            freq = QLabel(eqmod.BAND_LABELS[index], self)
            freq.setFont(T.semibold(9))
            freq.setStyleSheet(f"color: {T.DIM}; background: transparent;")
            freq.setFixedHeight(16)
            freq.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._freq_labels.append(freq)

        self._layout_page()

    # ----- layout ----------------------------------------------------------

    def set_page_width(self, width: int) -> None:
        width = max(900, int(width))
        if width != self.width():
            self.setFixedSize(width, T.STRIP_H)
            self._layout_page()

    def _layout_page(self) -> None:
        width = self.width()
        self.reset_chip.move(width - _M - self.reset_chip.width(), 12)
        self.toggle.move(self.reset_chip.x() - 18 - Toggle.W, 15)
        self.enabled_label.move(
            self.toggle.x() - 10 - self.enabled_label.width(), 20
        )

        x0 = T.EQ_BANDS_X
        span = width - _M - x0
        col = span / eqmod.BAND_COUNT
        for index in range(eqmod.BAND_COUNT):
            cx = x0 + col * index + col / 2
            slider = self.sliders[index]
            slider.move(int(cx - slider.width() / 2), _SLIDER_Y)
            value = self._value_labels[index]
            value.setFixedWidth(int(col))
            value.move(int(cx - col / 2), _VALUE_Y)
            freq = self._freq_labels[index]
            freq.setFixedWidth(int(col))
            freq.move(int(cx - col / 2), _FREQ_Y)
        self.update()

    # ----- state ---------------------------------------------------------

    def set_channel(self, key: str, label: str, accent: str, state: eqmod.ChannelEq) -> None:
        self._loading = True
        self.channel_key = key
        self.accent = accent
        self.title.setText(f"{label} EQUALIZER")
        self.title.setStyleSheet(f"color: {T.TEXT}; background: transparent;")
        state = state.normalized()
        self.toggle.set_value(state.enabled)
        for index, slider in enumerate(self.sliders):
            slider.set_accent(accent)
            slider.set_value(state.gains[index])
        self.preamp.set_accent(accent)
        self.preamp.set_value(state.preamp)
        self.note.setText(
            "The sliders icon opens that category's EQ. No need for direct "
            "EasyEffects tuning."
        )
        self._refresh_labels()
        self._loading = False
        self.update()

    def current_state(self) -> eqmod.ChannelEq:
        return eqmod.ChannelEq(
            enabled=self.toggle.value,
            preamp=self.preamp.value,
            gains=[slider.value for slider in self.sliders],
        )

    # ----- animation --------------------------------------------------------

    def play_entrance(self) -> None:
        """Start every fader flat: boosts slide up, cuts slide down into place."""
        for slider in self.sliders:
            slider.reset_display()
        self.preamp.reset_display()

    def tick(self, _now: float) -> bool:
        moving = False
        for slider in self.sliders:
            moving = slider.tick() or moving
        return self.preamp.tick() or moving

    # ----- internals ------------------------------------------------------

    def _refresh_labels(self) -> None:
        for index, slider in enumerate(self.sliders):
            value = slider.value
            label = self._value_labels[index]
            label.setText(f"{value:+.1f}".rstrip("0").rstrip(".") if value else "0")
            color = self.accent if value else T.DIM
            label.setStyleSheet(f"color: {color}; background: transparent;")
        self.preamp_value.setText(f"{self.preamp.value:+.1f} dB")

    def _emit(self) -> None:
        if not self._loading and self.channel_key:
            self.eqChanged.emit(self.channel_key)

    def _on_band(self, _index: int) -> None:
        self._refresh_labels()
        self._emit()

    def _on_any_change(self, *_args) -> None:
        self._refresh_labels()
        self._emit()

    def _apply_preset(self, name: str) -> None:
        gains = eqmod.PRESETS.get(name)
        if gains is None:
            return
        for slider, gain in zip(self.sliders, gains):
            slider.set_value(float(gain))
        self.preamp.set_value(eqmod.preset_preamp(gains))
        if not self.toggle.value:
            self.toggle.set_value(True)
        self._refresh_labels()
        self._emit()

    def _reset(self) -> None:
        for slider in self.sliders:
            slider.set_value(0.0)
        self.preamp.set_value(0.0)
        self._refresh_labels()
        self._emit()

    # ----- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(T.PANEL)))
        painter.drawRoundedRect(
            QRectF(0, 0, self.width(), self.height()), T.CARD_R, T.CARD_R
        )

        painter.setBrush(QBrush(QColor(self.accent)))
        painter.drawEllipse(QPointF(_M + self.back_chip.width() + 24, 28), 6, 6)

        # Column divider between the preset column and the band area.
        painter.setPen(QPen(QColor(T.RULE), 1.2))
        div_x = _M + T.EQ_LEFT_COL + 16
        painter.drawLine(QPointF(div_x, 68), QPointF(div_x, self.height() - 28))

        # dB guides behind the band sliders.
        top = _SLIDER_Y + 8.0
        bottom = _SLIDER_Y + T.EQ_SLIDER_H - 8.0
        mid = (top + bottom) / 2
        left = float(T.EQ_BANDS_X)
        right = float(self.width() - _M)
        painter.setPen(QPen(QColor(T.RULE), 1.0))
        painter.drawLine(QPointF(left, top), QPointF(right, top))
        painter.drawLine(QPointF(left, bottom), QPointF(right, bottom))
        painter.setPen(QPen(QColor(T.TRACK), 1.2))
        painter.drawLine(QPointF(left, mid), QPointF(right, mid))

        painter.setFont(T.font(8))
        painter.setPen(QColor(T.DIM))
        painter.drawText(QPointF(left - 26, top + 3), f"+{int(eqmod.GAIN_LIMIT)}")
        painter.drawText(QPointF(left - 22, mid + 3), "0")
        painter.drawText(QPointF(left - 26, bottom + 3), f"-{int(eqmod.GAIN_LIMIT)}")
        painter.end()
