"""Session D-Bus API so KWin can toggle the panel with a global shortcut."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

from .config import DBUS_INTERFACE, DBUS_PATH, DBUS_SERVICE


@pyqtClassInfo("D-Bus Interface", DBUS_INTERFACE)
class _Adaptor(QDBusAbstractAdaptor):

    def __init__(self, host: "BusApi") -> None:
        super().__init__(host)
        self._host = host

    @pyqtSlot()
    def Toggle(self) -> None:
        self._host.toggled()

    @pyqtSlot()
    def Show(self) -> None:
        self._host.shown()

    @pyqtSlot()
    def Hide(self) -> None:
        self._host.hidden()


class BusApi(QObject):
    def __init__(self, panel) -> None:
        super().__init__(panel)
        self._panel = panel
        self._adaptor = _Adaptor(self)

    def toggled(self) -> None:
        self._panel.toggle()

    def shown(self) -> None:
        self._panel.show_panel()

    def hidden(self) -> None:
        self._panel.hide_panel()

    def register(self) -> bool:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        if not bus.registerObject(
            DBUS_PATH,
            self,
            QDBusConnection.RegisterOption.ExportAdaptors,
        ):
            bus.unregisterObject(DBUS_PATH)
            if not bus.registerObject(
                DBUS_PATH,
                self,
                QDBusConnection.RegisterOption.ExportAdaptors,
            ):
                return False
        return bus.registerService(DBUS_SERVICE)
