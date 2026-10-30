"""Socket.IO client for Homely live events."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable
from urllib.parse import urlencode

import socketio

_LOGGER = logging.getLogger(__name__)

DEFAULT_WS_URL = "https://sdk.iotiliti.cloud"


class HomelyWebSocket:
    """One Socket.IO connection for one Homely location.

    Reconnects are driven by the caller (see Bridge) so that each attempt
    uses a fresh access token in the connection URL.
    """

    def __init__(
        self,
        location_id: str,
        on_event: Callable[[dict[str, Any]], None],
        on_status: Callable[[bool, str], None],
        url: str = DEFAULT_WS_URL,
    ):
        self._location_id = location_id
        self._on_event = on_event
        self._on_status = on_status
        self._url = url
        self._sio: socketio.Client | None = None
        self._lock = threading.Lock()
        self.connected = False

    def connect(self, token: str) -> None:
        """Connect using the given access token. Raises on failure."""
        with self._lock:
            self._close()
            # Never enable the Socket.IO/Engine.IO loggers: they log the
            # connection URL, which carries the access token.
            sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)

            # Callbacks from a replaced connection (closed in the background)
            # must not touch the state of the current one
            def current() -> bool:
                return self._sio is sio

            @sio.event
            def connect() -> None:
                if not current():
                    return
                self.connected = True
                _LOGGER.info("Live updates connected")
                self._on_status(True, "connected")

            @sio.event
            def disconnect(*args: Any) -> None:
                if not current():
                    return
                reason = str(args[0]) if args else "disconnected"
                was_connected = self.connected
                self.connected = False
                if was_connected:
                    _LOGGER.warning("Live updates disconnected (%s)", reason)
                self._on_status(False, reason)

            @sio.on("event")
            def event(data: Any) -> None:
                if not current():
                    return
                if isinstance(data, dict):
                    self._on_event(data)
                else:
                    _LOGGER.debug("Ignoring non-object event %r", data)

            @sio.on("*")
            def catch_all(name: str, data: Any = None) -> None:
                _LOGGER.debug("Unhandled Socket.IO event %s", name)

            # Homely requires the token in the query string; the header is
            # sent too, as the API document describes.
            query = urlencode({"locationId": self._location_id, "token": f"Bearer {token}"})
            self._sio = sio
            sio.connect(
                f"{self._url}?{query}",
                headers={"Authorization": f"Bearer {token}"},
                transports=["websocket"],
                wait_timeout=15,
            )

    def _close(self, wait: bool = False) -> None:
        sio, self._sio = self._sio, None
        self.connected = False
        if sio is None:
            return

        def disconnect() -> None:
            try:
                sio.disconnect()
            except Exception:  # noqa: BLE001 - best effort shutdown
                pass

        if wait:
            disconnect()
        else:
            # Closing a dead connection can block until its socket times out;
            # never let that hold up a reconnect
            threading.Thread(target=disconnect, name="homely-ws-close", daemon=True).start()

    def close(self) -> None:
        with self._lock:
            self._close(wait=True)
