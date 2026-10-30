"""Entry point: python -m homely_bridge"""

from __future__ import annotations

import logging
import signal
import sys
import threading

from . import __version__, config
from .api import HomelyAPI, HomelyApiError, HomelyAuthError
from .bridge import Bridge
from .mqtt import MqttPublisher

_LOGGER = logging.getLogger("homely_bridge")


def main() -> int:
    cfg = config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    # These libraries are chatty at debug level and may log URLs with tokens
    for noisy in ("urllib3", "socketio", "engineio", "websocket"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _LOGGER.info("Homely add-on %s starting", __version__)

    if not cfg.username or not cfg.password:
        _LOGGER.error("Set your Homely username and password in the add-on configuration")
        return 1

    stop = threading.Event()

    def handle_signal(signum, frame) -> None:
        _LOGGER.info("Stopping")
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    bridge: Bridge | None = None
    publisher = MqttPublisher(
        cfg.mqtt_host,
        cfg.mqtt_port,
        cfg.mqtt_username,
        cfg.mqtt_password,
        availability_topic=f"{cfg.base_topic}/bridge/availability",
        discovery_prefix=cfg.discovery_prefix,
        on_reconnect=lambda: bridge.republish_all() if bridge else None,
    )
    if not publisher.start():
        _LOGGER.warning("No MQTT connection yet, will keep trying in the background")

    api = HomelyAPI(cfg.username, cfg.password, base_url=cfg.api_url)
    bridge = Bridge(cfg, api, publisher)
    try:
        if bridge.setup(stop):
            bridge.run(stop)
    except HomelyAuthError as err:
        _LOGGER.error("Homely rejected the login: %s. Check username and password", err)
        return 1
    except HomelyApiError as err:
        _LOGGER.error("%s", err)
        return 1
    finally:
        bridge.close()
        publisher.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
