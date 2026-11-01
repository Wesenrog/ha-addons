"""Change detection. Each finding is a row: time, device, kind, one-line text.

Condition findings (link quality, errors, not heard) stay open while the
condition holds and close when it clears. Event findings (parent changed) close
after `event_finding_days`. A finding's `key` keeps one open row per condition.
"""
import logging
from datetime import timedelta
from statistics import median

from .logpatterns import SCAN_KINDS
from .timeutil import intersect, overlap

_LOG = logging.getLogger(__name__)

DAY = 86400

KIND_TEXT = {
    "route_error": "rutefeil",
    "network_status": "nettverksstatusfeil",
    "delivery_failed": "leveringsfeil",
    "timeout": "tidsavbrudd",
    "publish_failed": "mislykkede kommandoer",
    "ping_failed": "mislykkede ping",
    "read_failed": "mislykkede lesinger",
}
# Kinds not attributable to a device's own health.
_NOT_BASELINED = SCAN_KINDS | {"delivery_failed_broadcast"}
# A device's baseline needs this many days on which the add-on was listening.
MIN_BASELINE_DAYS = 7


class Detector:
    def __init__(self, store, inventory, ingest, cfg, local):
        self.store = store
        self.inventory = inventory
        self.ingest = ingest
        self.cfg = cfg
        self.local = local
        self.listening_since = None    # start of the current connected period

    # --- findings ------------------------------------------------------------
    def open(self, key, device_id, kind, text, now):
        row = self.store.one("SELECT id, text FROM finding WHERE key = ? AND closed_ts IS NULL", (key,))
        if row:
            if row["text"] != text:
                self.store.execute("UPDATE finding SET text = ? WHERE id = ?", (text, row["id"]))
            return False
        self.store.execute("INSERT INTO finding (ts, device_id, kind, key, text) VALUES (?,?,?,?,?)",
                           (now, device_id, kind, key, text))
        _LOG.info("finding: %s", text)
        return True

    def close(self, key, now):
        self.store.execute("UPDATE finding SET closed_ts = ? WHERE key = ? AND closed_ts IS NULL",
                           (now, key))

    def open_findings(self):
        return self.store.all("SELECT * FROM finding WHERE closed_ts IS NULL ORDER BY ts DESC, id DESC")

    def name_of(self, device_id):
        dev = self.inventory.devices.get(device_id)
        return dev.name if dev else f"#{device_id}"

    # --- coverage --------------------------------------------------------------
    def listening_intervals(self, now):
        rows = [(r["start"], r["end"]) for r in self.store.all("SELECT start, end FROM listening")]
        if self.listening_since is not None:
            rows.append((self.listening_since, now))
        return rows

    def z2m_online_intervals(self, now):
        out, start = [], None
        for r in self.store.all("SELECT ts, state FROM bridge_state ORDER BY ts, rowid"):
            if r["state"] == "online" and start is None:
                start = r["ts"]
            elif r["state"] == "offline" and start is not None:
                out.append((start, r["ts"]))
                start = None
        if start is not None:
            out.append((start, now))
        return out

    def observed_listening(self, start, end):
        """Share of [start, end) during which this add-on was listening."""
        return overlap(self.listening_intervals(end), start, end) / (end - start)

    def observed(self, start, end, now):
        """Seconds of [start, end) during which both this add-on and
        Zigbee2MQTT were up: the only time in which silence means anything."""
        both = intersect(self.listening_intervals(now), self.z2m_online_intervals(now))
        return overlap(both, start, end)

    # --- rule: parent changed (after each scan) ------------------------------
    def parents_after_scan(self, scan_id, now):
        current = self.store.all(
            "SELECT device_id, parent_id FROM parent WHERE scan_id = ? AND parent_id IS NOT NULL",
            (scan_id,))
        found = 0
        for row in current:
            prev = self.store.one(
                "SELECT p.parent_id FROM parent p JOIN scan s ON s.id = p.scan_id"
                " WHERE p.device_id = ? AND p.scan_id < ? AND p.parent_id IS NOT NULL"
                " AND s.status = 'ok' ORDER BY p.scan_id DESC LIMIT 1",
                (row["device_id"], scan_id))
            if prev and prev["parent_id"] != row["parent_id"]:
                dev = row["device_id"]
                text = (f"{self.name_of(dev)} har byttet forelder:"
                        f" {self.name_of(prev['parent_id'])} → {self.name_of(row['parent_id'])}")
                found += self.open(f"parent:{dev}:{scan_id}", dev, "parent_changed", text, now)
        return found

    # --- rule: link quality falling (daily and after each scan) ----------------
    def lqi(self, now):
        week = 7 * DAY
        found = 0
        for dev in self.inventory.present():
            if dev.type == "Coordinator":
                continue
            cur = [r[0] for r in self.store.all(
                "SELECT lqi FROM lqi WHERE device_id = ? AND bucket >= ? AND bucket < ?",
                (dev.id, now - week, now))]
            prev = [r[0] for r in self.store.all(
                "SELECT lqi FROM lqi WHERE device_id = ? AND bucket >= ? AND bucket < ?",
                (dev.id, now - 2 * week, now - week))]
            found += self._lqi_rule(f"lqi:{dev.id}", dev.id, prev, cur, now,
                                    f"Linkkvalitet for {dev.name}")
        # Links from the network maps, by IEEE pair.
        rows = self.store.all(
            "SELECT l.source_ieee, l.target_ieee, l.source_id, l.target_id, l.lqi, s.requested"
            " FROM scan_link l JOIN scan s ON s.id = l.scan_id"
            " WHERE s.status = 'ok' AND s.requested >= ? AND l.lqi IS NOT NULL",
            (now - 2 * week,))
        links = {}
        for r in rows:
            entry = links.setdefault((r["source_ieee"], r["target_ieee"]),
                                     {"ids": (r["source_id"], r["target_id"]), "prev": [], "cur": []})
            entry["cur" if r["requested"] >= now - week else "prev"].append(r["lqi"])
        for (src, tgt), e in links.items():
            s_id, t_id = e["ids"]
            label = (f"Link {self.name_of(s_id) if s_id else src} → "
                     f"{self.name_of(t_id) if t_id else tgt}")
            found += self._lqi_rule(f"linklqi:{src}:{tgt}", s_id or t_id, e["prev"], e["cur"],
                                    now, label, min_samples=min(self.cfg.lqi_min_samples, 4))
        return found

    def _lqi_rule(self, key, device_id, prev, cur, now, label, min_samples=None):
        need = min_samples or self.cfg.lqi_min_samples
        if len(prev) < need or len(cur) < need:
            return 0           # too little data either way: neither open nor close
        before, after = median(prev), median(cur)
        if before - after >= self.cfg.lqi_drop:
            return self.open(key, device_id, "lqi_drop",
                             f"{label} har falt fra {before:.0f} til {after:.0f} (median 7 døgn)", now)
        self.close(key, now)
        return 0

    # --- rule: errors above the device's own baseline (daily) -----------------
    def daily_counts(self, day):
        start, end = self.local.day_bounds(day)
        return {(r["device_id"], r["kind"]): r["n"] for r in self.store.all(
            "SELECT device_id, kind, COUNT(*) AS n FROM log_event"
            " WHERE ts >= ? AND ts < ? AND device_id IS NOT NULL GROUP BY device_id, kind",
            (start, end))}

    def errors(self, day, now):
        """Judge one complete local day against the days before it."""
        listening = self.listening_intervals(now)
        baseline_days = []
        for i in range(1, self.cfg.error_baseline_days + 1):
            d = day - timedelta(days=i)
            s, e = self.local.day_bounds(d)
            if overlap(listening, s, e) >= 0.5 * (e - s):
                baseline_days.append(d)
        if len(baseline_days) < MIN_BASELINE_DAYS:
            return 0
        history = [self.daily_counts(d) for d in baseline_days]
        today = self.daily_counts(day)
        found = 0
        keys = set(today) | {k for h in history for k in h}
        for device_id, kind in keys:
            if kind in _NOT_BASELINED:
                continue
            n = today.get((device_id, kind), 0)
            base = median(h.get((device_id, kind), 0) for h in history)
            key = f"errors:{device_id}:{kind}"
            if n > self.cfg.error_min_count and n > self.cfg.error_factor * max(base, 1):
                text = (f"{KIND_TEXT.get(kind, kind).capitalize()} for {self.name_of(device_id)}:"
                        f" {n} {day.isoformat()} mot normalt {base:g} per døgn")
                found += self.open(key, device_id, "errors", text, now)
            else:
                self.close(key, now)
        return found

    # --- rule: not heard (every few minutes) -----------------------------------
    def not_heard(self, now):
        """A device not heard for longer than its availability timeout, counting
        only time during which this add-on and Zigbee2MQTT were both up.

        Zigbee2MQTT's pings of mains devices and the frames it does not publish
        are invisible here, so a device Zigbee2MQTT currently reports online is
        taken as heard."""
        found = 0
        both = intersect(self.listening_intervals(now), self.z2m_online_intervals(now))
        for dev in self.inventory.present():
            if dev.type == "Coordinator":
                continue
            key = f"silent:{dev.id}"
            if self.ingest.availability.get(dev.id) == "online":
                self.close(key, now)
                continue
            last = self.ingest.heard.get(dev.id)
            if last is None:
                row = self.store.one("SELECT first_seen FROM device WHERE id = ?", (dev.id,))
                last = row["first_seen"]
            timeout = (self.cfg.timeout_battery_min if dev.battery else self.cfg.timeout_mains_min) * 60
            if overlap(both, last, now) > timeout:
                text = f"{dev.name} er ikke hørt siden {self.local.format(last)}"
                found += self.open(key, dev.id, "not_heard", text, now)
            else:
                self.close(key, now)
        # A device that left the network is no longer silent, just gone.
        for dev in self.inventory.devices.values():
            if dev.removed_at is not None:
                self.close(f"silent:{dev.id}", now)
        return found

    def expire_events(self, now):
        self.store.execute(
            "UPDATE finding SET closed_ts = ? WHERE closed_ts IS NULL AND kind = 'parent_changed'"
            " AND ts < ?", (now, now - self.cfg.event_finding_days * DAY))
