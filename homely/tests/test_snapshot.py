import copy

from homely_bridge.bridge import Bridge
from homely_bridge.config import Config


class RecordingPublisher:
    availability_topic = "homely/bridge/availability"

    def __init__(self):
        self.published = []

    def publish(self, topic, payload, retain=True):
        self.published.append((topic, payload))

    def last(self, topic):
        for t, p in reversed(self.published):
            if t == topic:
                return p
        return None


def make_bridge(home):
    pub = RecordingPublisher()
    bridge = Bridge(Config(username="u", password="p"), api=None, publisher=pub)
    bridge.location = {"name": home["name"], "locationId": home["locationId"]}
    bridge.location_id = home["locationId"]
    return bridge, pub


def test_device_removed_only_after_two_missing_snapshots(home_payload):
    bridge, pub = make_bridge(home_payload)
    bridge.apply_snapshot(home_payload)
    gone = home_payload["devices"][3]["id"]
    topic = f"homeassistant/binary_sensor/homely_{gone}/alarm_alarm/config"
    assert pub.last(topic)

    smaller = copy.deepcopy(home_payload)
    smaller["devices"] = smaller["devices"][:3]
    bridge.apply_snapshot(smaller)
    assert gone in bridge.devices
    bridge.apply_snapshot(smaller)
    assert gone not in bridge.devices
    assert pub.last(topic) == ""


def test_discovery_not_resent_when_unchanged(home_payload):
    bridge, pub = make_bridge(home_payload)
    bridge.apply_snapshot(home_payload)
    count = sum(1 for t, _ in pub.published if t.endswith("/config"))
    bridge.apply_snapshot(home_payload)
    assert sum(1 for t, _ in pub.published if t.endswith("/config")) == count


def test_unknown_device_event_requests_poll(home_payload):
    bridge, pub = make_bridge(home_payload)
    bridge.apply_snapshot(home_payload)
    bridge._next_poll = float("inf")
    bridge.handle_event({"type": "device-state-changed", "data": {"deviceId": "new", "changes": []}})
    assert bridge._next_poll != float("inf")


def test_snapshot_keeps_alarm_metadata_when_state_unchanged(home_payload):
    bridge, pub = make_bridge(home_payload)
    bridge.handle_event({"type": "alarm-state-changed", "data": {"state": "DISARMED", "userName": "Ola"}})
    bridge.apply_snapshot(home_payload)
    assert bridge.alarm["changed_by"] == "Ola"
