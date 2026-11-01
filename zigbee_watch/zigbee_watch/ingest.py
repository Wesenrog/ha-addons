"""Device messages (link quality, last heard), availability and bridge state.

Zigbee2MQTT republishes every device's *cached* state, with the cached
`linkquality`, at startup (z2m:controller.js:208-212, cache_state_send_on_startup)
and 30 s after Home Assistant reports online (z2m:extension/homeassistant.js:1913-1922).
Those messages look exactly like fresh ones, so for a while after either event
device messages are not taken as "heard" and their link quality is not stored.
A retained device message (a device with `retain: true`) is never fresh either.
"""
import json, logging
from datetime import datetime

_LOG = logging.getLogger(__name__)

# Seconds after Zigbee2MQTT comes online during which device messages may be
# its cached-state republish.
REPLAY_AFTER_Z2M_ONLINE = 120
# Zigbee2MQTT waits 30 s after homeassistant/status online, then republishes.
REPLAY_AFTER_HA_ONLINE = 150


def parse_json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def state_of(raw):
    """Availability and bridge state: {"state": "online"} (Zigbee2MQTT 2.x) or
    the legacy plain string."""
    value = parse_json(raw)
    if isinstance(value, dict):
        value = value.get("state")
    elif value is None and isinstance(raw, (str, bytes)):
        value = raw.decode() if isinstance(raw, bytes) else raw
    return value if value in ("online", "offline") else None


def parse_last_seen(value):
    """Zigbee2MQTT's optional `last_seen`: epoch milliseconds or ISO 8601."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1e12:
        return int(value / 1000)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


class Ingest:
    def __init__(self, store, cfg):
        self.store = store
        self.bucket = cfg.lqi_bucket_min * 60
        self.replay_until = 0
        self.heard = {r["device_id"]: r["ts"] for r in store.all("SELECT * FROM heard")}
        self.availability = {
            r["device_id"]: r["state"] for r in store.all(
                "SELECT device_id, state FROM availability a WHERE ts ="
                " (SELECT MAX(ts) FROM availability WHERE device_id = a.device_id)")}
        last = store.one("SELECT ts, state FROM bridge_state ORDER BY ts DESC, rowid DESC LIMIT 1")
        self.z2m_state = None          # unknown until the retained message arrives
        self._last_stored_z2m = last["state"] if last else None
        self.z2m_online_since = last["ts"] if last and last["state"] == "online" else None

    # --- Zigbee2MQTT and Home Assistant state ---------------------------------
    def on_bridge_state(self, raw, now):
        state = state_of(raw)
        if state is None:
            return None
        previous, self.z2m_state = self.z2m_state, state
        if state != self._last_stored_z2m:
            self.store.execute("INSERT INTO bridge_state VALUES (?,?)", (now, state))
            self._last_stored_z2m = state
            if state == "online":
                self.z2m_online_since = now
                self.replay_until = max(self.replay_until, now + REPLAY_AFTER_Z2M_ONLINE)
                _LOG.info("Zigbee2MQTT came online")
            else:
                self.z2m_online_since = None
                _LOG.warning("Zigbee2MQTT went offline")
        elif state == "online" and previous == "offline":
            self.replay_until = max(self.replay_until, now + REPLAY_AFTER_Z2M_ONLINE)
        return state

    def on_ha_status(self, raw, now):
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        if text.strip().lower() == "online":
            self.replay_until = max(self.replay_until, now + REPLAY_AFTER_HA_ONLINE)
            return True
        return False

    # --- device topics --------------------------------------------------------
    def on_device(self, dev, raw, now, retained=False):
        payload = parse_json(raw)
        if not isinstance(payload, dict):
            return False
        heard = parse_last_seen(payload.get("last_seen"))
        fresh = not retained and now >= self.replay_until
        if heard is None and fresh:
            heard = now
        if heard is not None and heard <= now:
            self._heard(dev.id, heard)
        lqi = payload.get("linkquality")
        if fresh and isinstance(lqi, int) and not isinstance(lqi, bool) and 0 <= lqi <= 255:
            bucket = now - now % self.bucket
            # Last value per bucket wins.
            self.store.execute(
                "INSERT INTO lqi VALUES (?,?,?) ON CONFLICT(device_id, bucket)"
                " DO UPDATE SET lqi = excluded.lqi", (dev.id, bucket, lqi))
            return True
        return False

    def on_availability(self, dev, raw, now):
        state = state_of(raw)
        if state is None or self.availability.get(dev.id) == state:
            return None
        self.availability[dev.id] = state
        self.store.execute("INSERT INTO availability VALUES (?,?,?)", (now, dev.id, state))
        return state

    def _heard(self, device_id, ts):
        if ts > self.heard.get(device_id, 0):
            self.heard[device_id] = ts
            self.store.execute(
                "INSERT INTO heard VALUES (?,?) ON CONFLICT(device_id)"
                " DO UPDATE SET ts = MAX(ts, excluded.ts)", (device_id, ts))

    def seen_in_scan(self, device_id, ts):
        """Zigbee2MQTT's own lastSeen from a network map: it hears frames it
        never publishes (check-ins, pings), so this keeps quiet devices from
        looking silent."""
        if ts:
            self._heard(device_id, ts)
