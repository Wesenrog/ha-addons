"""Wiring: the MQTT thread puts events on a queue, one worker thread owns the
database and the Watch."""
import logging, queue, threading, time

from .mqtt import MqttLink
from .store import Store
from .watch import Watch

_LOG = logging.getLogger(__name__)


class App:
    def __init__(self, cfg, clock=time.time, idle=1.0, client_id=""):
        self.cfg = cfg
        self.clock = clock
        self.idle = idle
        self.store = Store(cfg.db_path)
        self.events = queue.Queue()
        self.watch = Watch(cfg, self.store, lambda *a: self.link.publish(*a))
        self.link = MqttLink(
            cfg, self.watch.subscriptions, self._enqueue_message,
            lambda: self.events.put(("connected",)),
            lambda: self.events.put(("disconnected",)),
            client_id=client_id)
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="zigbee_watch", daemon=True)

    def _enqueue_message(self, topic, payload, retained):
        self.events.put(("message", topic, payload, retained))

    def start(self):
        self._worker.start()
        self.link.start()

    def stop(self):
        self._stop.set()
        self._worker.join(timeout=10)
        self.watch.stop(self.clock())
        self.link.stop()
        self.store.close()

    def _run(self):
        while not self._stop.is_set():
            try:
                event = self.events.get(timeout=self.idle)
            except queue.Empty:
                event = None
            now = int(self.clock())
            if event is not None:
                kind = event[0]
                if kind == "message":
                    self.watch.handle(event[1], event[2], event[3], now)
                elif kind == "connected":
                    self.watch.on_connected(now)
                elif kind == "disconnected":
                    self.watch.on_disconnected(now)
            self.watch.tick(now)
