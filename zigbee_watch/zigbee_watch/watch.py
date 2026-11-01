"""The core: routes messages to the parts and runs the schedule.

Single-threaded and clock-free: every call gets `now` (epoch seconds), so tests
drive it with a fake clock. app.py feeds it from the MQTT thread through a
queue.
"""
import json, logging, os

from . import entities
from .detect import Detector
from .ingest import Ingest, parse_json
from .inventory import Inventory
from .logs import LogCounter
from .networkmap import Scanner
from .timeutil import LocalTime, timedelta

_LOG = logging.getLogger(__name__)

NOT_HEARD_EVERY = 300
STATES_EVERY = 30
LISTENING_EVERY = 60


class Watch:
    def __init__(self, cfg, store, publish):
        self.cfg = cfg
        self.store = store
        self._publish = publish
        self.local = LocalTime(cfg.timezone)
        self.inventory = Inventory(store)
        self.ingest = Ingest(store, cfg)
        self.logs = LogCounter(store, self.inventory)
        self.scanner = Scanner(store, self.inventory, self.ingest, cfg, self.local, self.publish)
        self.scanner.on_finished = self._after_scan
        self.detector = Detector(store, self.inventory, self.ingest, cfg, self.local)
        self.connected = False
        self._published = {}
        self._listen_row = None
        self._next = {"not_heard": 0, "states": 0, "listening": 0}
        self._daily_done = None
        self._z2m = cfg.z2m_base_topic.rstrip("/") + "/"

    @property
    def subscriptions(self):
        return [self._z2m + "#", f"{self.cfg.discovery_prefix}/status"]

    def publish(self, topic, payload, retain=True):
        self._publish(topic, payload, retain)

    # --- connection ---------------------------------------------------------
    def on_connected(self, now):
        """Every connect, first or not: the broker may have restarted with a
        clean session and no retained messages."""
        self.connected = True
        cur = self.store.execute("INSERT INTO listening (start, end) VALUES (?, ?)", (now, now))
        self._listen_row = cur.lastrowid
        self.detector.listening_since = now
        self.publish_all(now)

    def on_disconnected(self, now):
        if self.connected:
            self._close_listening(now)
        self.connected = False

    def _close_listening(self, now):
        if self._listen_row is not None:
            self.store.execute("UPDATE listening SET end = ? WHERE id = ?", (now, self._listen_row))
        self._listen_row = None
        self.detector.listening_since = None

    def publish_all(self, now):
        self.publish(self.cfg.availability_topic, "online")
        for topic, payload in entities.discovery(self.cfg):
            self.publish(topic, payload)
        self._published.clear()
        self.publish_states(now)

    def publish_states(self, now):
        if not self.connected:
            return
        for topic, payload in entities.states(self, now).items():
            if self._published.get(topic) != payload:
                self.publish(topic, payload)
                self._published[topic] = payload

    # --- messages -------------------------------------------------------------
    def handle(self, topic, raw, retained, now):
        try:
            self._handle(topic, raw, retained, now)
        except Exception:                      # noqa: BLE001 - never die on a message
            _LOG.exception("message on %s could not be handled", topic)

    def _handle(self, topic, raw, retained, now):
        if topic == f"{self.cfg.discovery_prefix}/status":
            if self.ingest.on_ha_status(raw, now):
                _LOG.info("Home Assistant online - republishing")
                self.publish_all(now)
            return
        if not topic.startswith(self._z2m):
            return
        rest = topic[len(self._z2m):]
        if rest == "bridge/devices":
            self.inventory.update(parse_json(raw), now)
        elif rest == "bridge/state":
            if self.ingest.on_bridge_state(raw, now):
                self.publish_states(now)
        elif rest == "bridge/logging":
            self.logs.handle(parse_json(raw), now)
        elif rest == "bridge/response/networkmap":
            self.scanner.on_response(parse_json(raw), now)
        elif rest.startswith("bridge/"):
            return
        elif (dev := self.inventory.by_name.get(rest)) is not None:
            self.ingest.on_device(dev, raw, now, retained)
        elif rest.endswith("/availability") and \
                (dev := self.inventory.by_name.get(rest[:-len("/availability")])) is not None:
            self.ingest.on_availability(dev, raw, now)

    # --- schedule ------------------------------------------------------------
    def tick(self, now):
        try:
            self._tick(now)
        except Exception:                      # noqa: BLE001 - keep the schedule alive
            _LOG.exception("scheduled work failed")

    def _tick(self, now):
        self.scanner.tick(now)
        if now >= self._next["not_heard"]:
            self._next["not_heard"] = now + NOT_HEARD_EVERY
            self.detector.not_heard(now)
            self.detector.expire_events(now)
        today = self.local.date_of(now)
        # The daily work follows the night's scan (or its retry window), so the
        # snapshot holds the night's map.
        if self._daily_done != today and self.scanner.done_date == today:
            self._daily_done = today
            self.daily(now)
        if self.connected and now >= self._next["listening"]:
            self._next["listening"] = now + LISTENING_EVERY
            self.store.execute("UPDATE listening SET end = ? WHERE id = ?", (now, self._listen_row))
        if now >= self._next["states"]:
            self._next["states"] = now + STATES_EVERY
            self.publish_states(now)

    def daily(self, now):
        yesterday = self.local.date_of(now) - timedelta(days=1)
        self.detector.errors(yesterday, now)
        self.detector.lqi(now)
        self.store.prune(now, self.cfg.retention_days)
        if self.cfg.db_path != ":memory:":
            target = os.path.join(os.path.dirname(self.cfg.db_path), "zigbee_watch-copy.sqlite")
            try:
                self.store.snapshot(target)
            except Exception:                  # noqa: BLE001 - a full disk must not stop the watch
                _LOG.exception("snapshot to %s failed", target)
        self.publish_states(now)

    def _after_scan(self, scan_id, status, now):
        if scan_id is not None and status == "ok":
            self.detector.parents_after_scan(scan_id, now)
            self.detector.lqi(now)
        self.publish_states(now)

    def stop(self, now):
        self._close_listening(now)
        if self.connected:
            self.publish(self.cfg.availability_topic, "offline")
