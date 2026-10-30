"""Zigbee2MQTT's `bridge/logging`: classify, count, and sample the unknown."""
import logging, re

from . import logpatterns

_LOG = logging.getLogger(__name__)

SAMPLED_LEVELS = {"warning", "warn", "error"}
_DETAIL_MAX = 300

# Normalisation for grouping unmatched lines: one sample per shape, not one per
# address or number.
_NORMALISE = [
    (re.compile(r"0x[0-9a-fA-F]+"), "<hex>"),
    (re.compile(r"'[^']*'"), "'<s>'"),
    (re.compile(r'"[^"]*"'), '"<s>"'),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
]


def shape(text):
    for rx, repl in _NORMALISE:
        text = rx.sub(repl, text)
    return text[:200]


class LogCounter:
    def __init__(self, store, inventory):
        self.store = store
        self.inventory = inventory

    def handle(self, payload, now):
        if not isinstance(payload, dict):
            return None
        message, level = payload.get("message"), payload.get("level")
        if not isinstance(message, str) or not isinstance(level, str):
            return None
        hit = logpatterns.classify(message, level)
        if hit is None:
            if level.lower() in SAMPLED_LEVELS:
                self._sample(message, level, now)
            return None
        kind, who_type, who, detail = hit
        device_id = nwk = None
        if who_type == "nwk":
            nwk = int(who)
            dev = self.inventory.by_nwk.get(nwk)
            device_id = dev.id if dev else None
        elif who_type == "name":
            dev = self.inventory.find_name(who)
            device_id = dev.id if dev else None
            nwk = dev.network_address if dev else None
        if detail and len(detail) > _DETAIL_MAX:
            detail = detail[:_DETAIL_MAX] + "..."
        self.store.execute("INSERT INTO log_event VALUES (?,?,?,?,?)",
                           (now, device_id, nwk, kind, detail))
        return kind

    def _sample(self, message, level, now):
        ns, text = logpatterns.split_namespace(message)
        key = f"{level.lower()}|{ns or ''}|{shape(text)}"
        self.store.execute(
            "INSERT INTO log_unmatched (key, level, sample, count, first_ts, last_ts)"
            " VALUES (?,?,?,1,?,?) ON CONFLICT(key) DO UPDATE SET"
            " count = count + 1, last_ts = excluded.last_ts",
            (key, level.lower(), message[:1000], now, now))
