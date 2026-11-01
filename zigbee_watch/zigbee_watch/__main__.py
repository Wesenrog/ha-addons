"""Entry point: configure, start, stay running until the Supervisor stops us."""
import logging, signal, threading

from .app import App
from .config import Config


def main():
    cfg = Config.from_env()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    app = App(cfg)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    app.start()
    logging.getLogger("zigbee_watch").info("zigbee-watch running, database %s", cfg.db_path)
    done.wait()
    app.stop()


main()
