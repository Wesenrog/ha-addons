"""A Watch on a database file, driven directly with synthetic data."""
from conftest import DEVICES, OSLO
from zigbee_watch.config import Config
from zigbee_watch.store import Store
from zigbee_watch.watch import Watch

DAY = 86400


class Harness:
    def __init__(self, path, now, **cfg):
        self.cfg = Config(db_path=str(path), **cfg)
        self.store = Store(self.cfg.db_path)
        self.published = {}
        self.watch = Watch(self.cfg, self.store, self._publish)
        self.watch.inventory.update(DEVICES, now - 60 * DAY)
        self.det = self.watch.detector

    def _publish(self, topic, payload, retain=True):
        self.published[topic] = payload

    def dev(self, name):
        return self.watch.inventory.by_name[name].id

    def listening(self, start, end):
        self.store.execute("INSERT INTO listening (start, end) VALUES (?, ?)", (start, end))

    def z2m(self, ts, state):
        self.store.execute("INSERT INTO bridge_state VALUES (?, ?)", (ts, state))

    def route_errors(self, name, day, n, kind="route_error"):
        start, _ = OSLO.day_bounds(day)
        dev = self.watch.inventory.by_name[name]
        for i in range(n):
            self.store.execute("INSERT INTO log_event VALUES (?,?,?,?,?)",
                               (start + 3600 + i * 60, dev.id, dev.network_address, kind,
                                "ROUTE_ERROR_NON_TREE_LINK_FAILURE"))

    def lqi(self, name, start, end, value, step=600):
        dev = self.dev(name)
        for t in range(start - start % step, end, step):
            self.store.execute("INSERT OR REPLACE INTO lqi VALUES (?,?,?)", (dev, t, value))

    def open_findings(self, kind=None):
        rows = self.det.open_findings()
        return [r for r in rows if kind is None or r["kind"] == kind]

    def scan(self, value, now):
        sc = self.watch.scanner
        sc.request(now)
        txn = sc.pending["txn"]
        return sc.on_response({"status": "ok", "transaction": txn,
                               "data": {"type": "raw", "routes": True, "value": value}}, now)
