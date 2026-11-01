"""Keeps Home Assistant (via MQTT) in sync with one Homely location."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .api import HomelyAPI, HomelyApiError, HomelyAuthError, HomelyError, HomelyUnavailable
from .config import Config
from .model import (
    EntitySpec,
    ONLINE_SPEC,
    device_entities,
    device_has_problem,
    device_state_payload,
    entity_name,
    normalize_alarm_state,
    slugify,
    to_bool,
)
from .mqtt import MqttPublisher, discovery_payload, discovery_topic
from .websocket import HomelyWebSocket

_LOGGER = logging.getLogger(__name__)

POLL_BACKOFF = (60, 180, 600, 1800, 3600)
WS_BACKOFF = (5, 15, 30, 60, 120, 300)
# Minimum time between two polls that were not scheduled by poll_interval
MIN_POLL_GAP = 60
# A device must be missing from this many snapshots in a row before it is removed
MISSING_SNAPSHOTS_BEFORE_REMOVAL = 2


def _identity(value: Any) -> Any:
    return value


HUB_SPECS = (
    EntitySpec("alarm", "sensor", "alarm", _identity, icon="mdi:shield-home"),
    EntitySpec("armed", "binary_sensor", "armed", _identity, icon="mdi:shield-lock"),
    EntitySpec("triggered", "binary_sensor", "triggered", _identity, "safety"),
    EntitySpec("battery_problem", "binary_sensor", "battery_problem", _identity, "battery"),
    EntitySpec("tamper", "binary_sensor", "tamper", _identity, "tamper"),
    EntitySpec(
        "devices_offline", "sensor", "devices_offline", _identity, state_class="measurement",
        entity_category="diagnostic", icon="mdi:lan-disconnect",
    ),
    EntitySpec("live_updates", "binary_sensor", "live_updates", _identity, "connectivity", entity_category="diagnostic"),
    EntitySpec("last_poll", "sensor", "last_poll", _identity, "timestamp", entity_category="diagnostic"),
    EntitySpec("last_poll_status", "sensor", "last_poll_status", _identity, entity_category="diagnostic", icon="mdi:cloud-check"),
)
# Hub diagnostics explain why the rest is unavailable, so they only follow the process
HUB_DIAGNOSTICS = {"devices_offline", "live_updates", "last_poll", "last_poll_status"}


def _suggested_area(location: Any) -> str | None:
    # Homely formats this as "Floor 1 - Entrance"; the room is the last part
    if not isinstance(location, str) or not location.strip():
        return None
    return location.split(" - ")[-1].strip() or None


class Bridge:
    def __init__(self, config: Config, api: HomelyAPI, publisher: MqttPublisher):
        self._config = config
        self._api = api
        self._mqtt = publisher
        self._lock = threading.RLock()
        self._ws: HomelyWebSocket | None = None

        self.location: dict[str, Any] = {}
        self.location_id = ""
        self.devices: dict[str, dict[str, Any]] = {}
        self._missing: dict[str, int] = {}
        self._discovery: dict[str, dict[str, Any]] = {}
        self._device_topics: dict[str, set[str]] = {}
        self.alarm: dict[str, Any] = {}
        self._live = False
        # Data availability: live only after a snapshot fetched while the
        # current websocket connection was up, and until the websocket has
        # been down for longer than stale_after
        self._ws_epoch = 0
        self._ws_down_since: float | None = None
        self._need_fresh_snapshot = True
        self._data_online: bool | None = None
        self._device_online: dict[str, bool] = {}
        self._last_poll: str | None = None
        self._last_poll_status = "not_run"
        self._have_snapshot = False

        now = time.monotonic()
        self._next_poll = now
        self._last_poll_attempt = 0.0
        self._last_poll_success = 0.0
        self._poll_failures = 0
        self._poll_not_before = 0.0
        self._next_ws_attempt = now
        self._ws_failures = 0
        self._ws_connected_at = 0.0
        self._ws_ever_connected = False

    # -- topics ---------------------------------------------------------------

    @property
    def _base(self) -> str:
        return f"{self._config.base_topic}/{self.location_id}"

    def _hub_state_topic(self) -> str:
        return f"{self._base}/state"

    def _alarm_attributes_topic(self) -> str:
        return f"{self._base}/alarm"

    def _device_state_topic(self, device_id: str) -> str:
        return f"{self._base}/{device_id}/state"

    def _data_availability_topic(self) -> str:
        return f"{self._base}/availability"

    def _device_availability_topic(self, device_id: str) -> str:
        return f"{self._base}/{device_id}/availability"

    def _hub_device(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "identifiers": [f"homely_{self.location_id}"],
            "name": f"Homely {self.location.get('name') or self.location_id}",
            "manufacturer": "Homely",
            "model": entity_name("hub_model", self._config.language),
        }
        if self.location.get("gatewayserial"):
            info["serial_number"] = self.location["gatewayserial"]
        return info

    @staticmethod
    def _device_name(device: dict[str, Any]) -> str:
        return device.get("name") or device.get("modelName") or device["id"]

    def _device_info(self, device: dict[str, Any]) -> dict[str, Any]:
        info: dict[str, Any] = {
            "identifiers": [f"homely_{device['id']}"],
            "name": self._device_name(device),
            "manufacturer": "Homely",
            "via_device": f"homely_{self.location_id}",
        }
        if device.get("modelName"):
            info["model"] = device["modelName"]
        if device.get("serialNumber"):
            info["serial_number"] = device["serialNumber"]
        area = _suggested_area(device.get("location"))
        if area:
            info["suggested_area"] = area
        return info

    # -- publishing (call with self._lock held) --------------------------------

    def _publish_discovery(self, topic: str, payload: dict[str, Any]) -> None:
        if self._discovery.get(topic) == payload:
            return
        self._mqtt.publish(topic, payload)
        self._discovery[topic] = payload

    def _publish_hub_discovery(self) -> None:
        node = self.location_id
        for spec in HUB_SPECS:
            payload = discovery_payload(
                spec,
                f"homely_{node}",
                self._hub_state_topic(),
                [self._mqtt.availability_topic]
                if spec.key in HUB_DIAGNOSTICS
                else [self._mqtt.availability_topic, self._data_availability_topic()],
                self._hub_device(),
                self._config.language,
                "homely",
                json_attributes_topic=self._alarm_attributes_topic() if spec.key == "alarm" else None,
            )
            self._publish_discovery(discovery_topic(self._config.discovery_prefix, spec.component, node, spec.key), payload)

    def _publish_hub_state(self) -> None:
        state = normalize_alarm_state(self.alarm.get("raw_state")) if self.alarm else "unknown"
        payload: dict[str, Any] = {
            "alarm": state,
            "armed": "ON" if state.startswith("armed_") else "OFF",
            "triggered": "ON" if state == "triggered" else "OFF",
            "live_updates": "ON" if self._live else "OFF",
            "last_poll": self._last_poll,
            "last_poll_status": self._last_poll_status,
            "devices_offline": None,
            "battery_problem": None,
            "tamper": None,
        }
        if not self.alarm:
            payload["armed"] = payload["triggered"] = None
        if self._have_snapshot:
            devices = list(self.devices.values())
            payload["devices_offline"] = sum(1 for d in devices if to_bool(d.get("online")) is False)
            payload["battery_problem"] = "ON" if any(
                device_has_problem(d, "battery", ("low", "defect"))
                or device_has_problem(d, "report", ("lowbat",))
                for d in devices
            ) else "OFF"
            payload["tamper"] = "ON" if any(device_has_problem(d, "alarm", ("tamper",)) for d in devices) else "OFF"
        self._mqtt.publish(self._hub_state_topic(), payload)
        self._mqtt.publish(self._alarm_attributes_topic(), {
            "raw_state": self.alarm.get("raw_state"),
            "changed_by": self.alarm.get("changed_by"),
            "changed_at": self.alarm.get("changed_at"),
        })

    def _publish_device(self, device: dict[str, Any]) -> None:
        device_id = device["id"]
        info = self._device_info(device)
        entity_id_prefix = f"homely_{slugify(self._device_name(device))}"
        topics = self._device_topics.setdefault(device_id, set())
        # The Online entity must stay readable while the device is offline
        with_device = [self._mqtt.availability_topic, self._data_availability_topic(),
                       self._device_availability_topic(device_id)]
        without_device = with_device[:2]
        for spec, _raw in device_entities(device):
            topic = discovery_topic(self._config.discovery_prefix, spec.component, device_id, spec.key)
            payload = discovery_payload(
                spec, f"homely_{device_id}", self._device_state_topic(device_id),
                without_device if spec is ONLINE_SPEC else with_device,
                info, self._config.language, entity_id_prefix,
            )
            self._publish_discovery(topic, payload)
            topics.add(topic)
        self._mqtt.publish(self._device_state_topic(device_id), device_state_payload(device))
        # Online unless Homely reports the device offline
        online = to_bool(device.get("online")) is not False
        if self._device_online.get(device_id) != online:
            self._mqtt.publish(self._device_availability_topic(device_id), "online" if online else "offline")
            self._device_online[device_id] = online

    def _remove_device(self, device_id: str) -> None:
        device = self.devices.pop(device_id, {})
        _LOGGER.info("Removing device %s, it is no longer reported by Homely", device.get("name", device_id))
        for topic in self._device_topics.pop(device_id, set()):
            self._mqtt.publish(topic, "")
            self._discovery.pop(topic, None)
        self._mqtt.publish(self._device_state_topic(device_id), "")
        self._mqtt.publish(self._device_availability_topic(device_id), "")
        self._device_online.pop(device_id, None)
        self._missing.pop(device_id, None)

    def republish_all(self) -> None:
        """Send all discovery and state messages again (HA or broker restart)."""
        with self._lock:
            if not self.location_id:
                return
            self._discovery.clear()
            self._device_online.clear()
            self._mqtt.publish(self._mqtt.availability_topic, "online")
            self._publish_data_availability(force=True)
            self._publish_hub_discovery()
            for device in self.devices.values():
                self._publish_device(device)
            self._publish_hub_state()

    # -- data availability (call with self._lock held) ---------------------------

    def _data_state(self) -> tuple[bool, str]:
        """Return (online, reason) for the data availability topic."""
        now = time.monotonic()
        stale_after = self._config.stale_after
        if (
            not self._need_fresh_snapshot
            and not self._live
            and self._ws_down_since is not None
            and now - self._ws_down_since >= stale_after
        ):
            # Events during the gap are lost; only a new snapshot can fix that
            self._need_fresh_snapshot = True
        if self._need_fresh_snapshot:
            if not self._have_snapshot and not self._ws_ever_connected:
                return False, "starting"
            if not self._live:
                return False, f"live updates disconnected for more than {stale_after}s"
            return False, "waiting for a fresh poll"
        return True, "live"

    def _publish_data_availability(self, force: bool = False) -> None:
        if not self.location_id:
            return
        online, reason = self._data_state()
        if online == self._data_online and not force:
            return
        if online != self._data_online:
            if online:
                _LOGGER.info("Data live")
            else:
                _LOGGER.info("Data stale: %s", reason)
        self._data_online = online
        self._mqtt.publish(self._data_availability_topic(), "online" if online else "offline")

    def update_availability(self) -> None:
        with self._lock:
            self._publish_data_availability()

    # -- data updates -----------------------------------------------------------

    def apply_snapshot(self, home: dict[str, Any], ws_epoch: int | None = None) -> None:
        """Apply a /home snapshot.

        ws_epoch is the websocket connection that was up when the snapshot
        was requested, or None. Only a snapshot taken during the current
        connection makes stale data live again.
        """
        with self._lock:
            raw = home.get("alarmState")
            if raw and raw != self.alarm.get("raw_state"):
                self.alarm = {"raw_state": raw}
            seen: set[str] = set()
            for device in home.get("devices") or []:
                device_id = device.get("id")
                if not device_id:
                    continue
                seen.add(device_id)
                self._missing.pop(device_id, None)
                is_new = device_id not in self.devices
                self.devices[device_id] = device
                if is_new and self._have_snapshot:
                    _LOGGER.info("New device %s (%s)", device.get("name"), device.get("modelName"))
                self._publish_device(device)
            for device_id in [d for d in self.devices if d not in seen]:
                self._missing[device_id] = self._missing.get(device_id, 0) + 1
                if self._missing[device_id] >= MISSING_SNAPSHOTS_BEFORE_REMOVAL:
                    self._remove_device(device_id)
                else:
                    _LOGGER.info("Device %s missing from snapshot, will remove if still missing next time", device_id)
            if not self._have_snapshot:
                _LOGGER.info("Found %d devices at %s", len(self.devices), self.location.get("name"))
            self._have_snapshot = True
            if ws_epoch is not None and ws_epoch == self._ws_epoch and self._live:
                self._need_fresh_snapshot = False
            self._publish_hub_state()
            self._publish_data_availability()

    def handle_event(self, event: dict[str, Any]) -> None:
        """Apply one websocket event. Runs on the Socket.IO thread."""
        event_type = event.get("type")
        data = event.get("data") or event.get("payload") or {}
        if not isinstance(data, dict):
            return
        with self._lock:
            if event_type == "alarm-state-changed":
                raw = data.get("state") or data.get("alarmState")
                if not raw:
                    return
                self.alarm = {
                    "raw_state": raw,
                    "changed_by": data.get("userName"),
                    "changed_at": data.get("timestamp"),
                }
                _LOGGER.info(
                    "Alarm state %s%s", raw, f" (by {data['userName']})" if data.get("userName") else ""
                )
                self._publish_hub_state()
            elif event_type == "device-state-changed":
                device = self.devices.get(data.get("deviceId"))
                if device is None:
                    _LOGGER.info("Event for unknown device %s, fetching devices", data.get("deviceId"))
                    self.request_poll()
                    return
                if "online" in data:
                    device["online"] = data["online"]
                for change in data.get("changes") or []:
                    feature, state = change.get("feature"), change.get("stateName")
                    if not feature or not state:
                        continue
                    states = device.setdefault("features", {}).setdefault(feature, {}).setdefault("states", {})
                    states[state] = {"value": change.get("value"), "lastUpdated": change.get("lastUpdated")}
                    _LOGGER.debug("%s: %s.%s = %r", device.get("name"), feature, state, change.get("value"))
                self._publish_device(device)
                self._publish_hub_state()
            elif data.get("deviceId") in self.devices and "online" in data:
                # Any other event that reports a device's online state
                device = self.devices[data["deviceId"]]
                device["online"] = data["online"]
                self._publish_device(device)
                self._publish_hub_state()
            else:
                _LOGGER.debug("Ignoring event type %s: %s", event_type, data)

    def _on_ws_status(self, connected: bool, reason: str) -> None:
        with self._lock:
            was_live = self._live
            self._live = connected
            if connected:
                self._ws_epoch += 1
                self._ws_down_since = None
            elif was_live:
                self._ws_down_since = time.monotonic()
            if not connected:
                now = time.monotonic()
                if self._ws_connected_at and now - self._ws_connected_at > 60:
                    # The connection was stable, so this is a fresh drop
                    self._ws_failures = 0
                self._ws_connected_at = 0.0
                self._next_ws_attempt = now + WS_BACKOFF[min(self._ws_failures, len(WS_BACKOFF) - 1)]
                self._ws_failures += 1
            if self.location_id:
                self._publish_hub_state()
                self._publish_data_availability()

    def request_poll(self, delay: float = 0.0) -> None:
        with self._lock:
            # Never earlier than a rate-limit backoff or Retry-After allows
            earliest = max(time.monotonic() + delay, self._last_poll_attempt + MIN_POLL_GAP, self._poll_not_before)
            self._next_poll = min(self._next_poll, earliest)

    # -- lifecycle --------------------------------------------------------------

    def select_location(self, locations: list[dict[str, Any]]) -> dict[str, Any]:
        if not locations:
            raise HomelyApiError("This Homely account has no homes where you are owner or admin")
        names = ", ".join(repr(loc.get("name")) for loc in locations)
        wanted = (self._config.home or "").strip()
        if not wanted:
            if len(locations) > 1:
                _LOGGER.info("Account has several homes (%s); using the first. Set 'home' to choose", names)
            return locations[0]
        for loc in locations:
            if wanted.lower() in (str(loc.get("name", "")).lower(), str(loc.get("locationId", "")).lower()):
                return loc
        raise HomelyApiError(f"Home {wanted!r} not found. Available homes: {names}")

    def setup(self, stop: threading.Event) -> bool:
        """Log in and pick the location. Returns False if stopped first."""
        delay_index = 0
        while not stop.is_set():
            try:
                locations = self._api.get_locations()
                break
            except HomelyUnavailable as err:
                delay = max(err.retry_after or 0, POLL_BACKOFF[min(delay_index, len(POLL_BACKOFF) - 1)])
                delay_index += 1
                _LOGGER.warning("Homely is unavailable (%s), retrying in %ds", err, delay)
                stop.wait(delay)
        else:
            return False
        location = self.select_location(locations)
        with self._lock:
            self.location = location
            self.location_id = location["locationId"]
            _LOGGER.info("Using Homely home %r", location.get("name"))
            self._publish_data_availability()
            self._publish_hub_discovery()
            self._publish_hub_state()
        self._ws = HomelyWebSocket(self.location_id, self.handle_event, self._on_ws_status, self._config.ws_url)
        return True

    def poll(self) -> None:
        now = time.monotonic()
        self._last_poll_attempt = now
        with self._lock:
            ws_epoch = self._ws_epoch if self._live else None
        try:
            home = self._api.get_home(self.location_id)
        except HomelyError as err:
            status = getattr(err, "status", None)
            if isinstance(err, HomelyAuthError):
                delay = 600
            else:
                retry_after = getattr(err, "retry_after", None) or 0
                delay = max(retry_after, POLL_BACKOFF[min(self._poll_failures, len(POLL_BACKOFF) - 1)])
                delay = min(delay, max(self._config.poll_interval, retry_after))
            self._poll_failures += 1
            with self._lock:
                self._last_poll_status = f"failed_{status}" if status else "failed"
                self._next_poll = now + delay
                # Homely answered (rate limit, server error): a reconnect must not
                # poll earlier. A network error has no such limit.
                self._poll_not_before = now + delay if status else 0.0
                self._publish_hub_state()
            level = logging.INFO if self._have_snapshot and isinstance(err, HomelyUnavailable) else logging.WARNING
            _LOGGER.log(level, "Could not fetch devices (%s); keeping last known state, retrying in %ds", err, delay)
            return
        self._poll_failures = 0
        self._poll_not_before = 0.0
        self._last_poll_success = now
        with self._lock:
            self._last_poll = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._last_poll_status = "success"
            self._next_poll = now + self._config.poll_interval
        self.apply_snapshot(home, ws_epoch)

    def _connect_ws(self) -> None:
        assert self._ws is not None
        now = time.monotonic()
        try:
            token = self._api.token()
            self._ws.connect(token)
        except Exception as err:  # noqa: BLE001 - socketio raises various errors
            delay = WS_BACKOFF[min(self._ws_failures, len(WS_BACKOFF) - 1)]
            self._ws_failures += 1
            _LOGGER.warning("Could not connect live updates (%s), retrying in %ds", err, delay)
            self._next_ws_attempt = now + delay
            return
        self._ws_connected_at = now
        # Events during a drop are lost, however short it was, and stale data
        # needs a snapshot from this connection: poll as soon as the rate
        # limits (MIN_POLL_GAP, backoff, Retry-After) allow
        self.request_poll()
        self._ws_ever_connected = True

    def run(self, stop: threading.Event) -> None:
        # Data availability runs on its own timer, so a slow poll or reconnect
        # never delays the switch to stale
        def availability_timer() -> None:
            while not stop.wait(0.5):
                self.update_availability()

        threading.Thread(target=availability_timer, name="homely-availability", daemon=True).start()
        while not stop.is_set():
            now = time.monotonic()
            if self._ws is not None and not self._ws.connected and now >= self._next_ws_attempt:
                self._connect_ws()
            if now >= self._next_poll:
                self.poll()
            stop.wait(1)

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
