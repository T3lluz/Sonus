"""Single instance guard and the toggle channel used by the global shortcut."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .config import APP_ID

SERVER_NAME = APP_ID
_CONNECT_MS = 300


def send(message: str = "toggle") -> bool:
    """Deliver a command to the running panel. False when nothing is listening."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(_CONNECT_MS):
        return False
    socket.write(message.encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(_CONNECT_MS)
    socket.disconnectFromServer()
    return True


def instance_running() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    connected = socket.waitForConnected(_CONNECT_MS)
    if connected:
        socket.disconnectFromServer()
    return connected


class CommandServer(QObject):
    """Listens for `--toggle` from the shortcut launcher."""

    toggled = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        if not self._server.listen(SERVER_NAME):
            # A socket file left behind by a crash blocks the bind.
            QLocalServer.removeServer(SERVER_NAME)
            if not self._server.listen(SERVER_NAME):
                return False
        return True

    def close(self) -> None:
        self._server.close()
        QLocalServer.removeServer(SERVER_NAME)

    def _on_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda s=socket: self._on_ready(s))
        socket.disconnected.connect(socket.deleteLater)

    def _on_ready(self, socket: QLocalSocket) -> None:
        message = bytes(socket.readAll()).decode("utf-8", errors="replace").strip()
        if message == "quit":
            self.quit_requested.emit()
        elif message == "toggle":
            self.toggled.emit()
