"""Availability, liveness and retraction (0.11.0): a mirrored entity is live
only while the tunnel, the add-on and its source all are, and nothing stays on
the broker for an entity that is no longer mirrored."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mirror_bridge import classes                                 # noqa: E402
from mirror_bridge.bridge import Bridge                           # noqa: E402
from mirror_bridge.config import Config                           # noqa: E402
from test_classes import CANCEL, SPEED, FakeBroker, FakeHA, bridge, run   # noqa: E402

BATTERY = {"entity_id": "sensor.valve_battery", "state": "87",
           "attributes": {"device_class": "battery", "state_class": "measurement",
                          "unit_of_measurement": "%", "friendly_name": "Valve Battery"}}
VALVE = {"entity_id": "climate.valve", "state": "heat",
         "attributes": {"hvac_modes": ["off", "heat"], "min_temp": 4, "max_temp": 35,
                        "current_temperature": 20.5, "temperature": 21,
                        "hvac_action": "idle", "friendly_name": "Valve"}}
ALIVE = "mirror/state/_bridge/alive"


class RegistryHA(FakeHA):
    """FakeHA with the registries: `labels` is entity_id -> the labels on it."""

    def __init__(self, states, labels):
        super().__init__(states)
        self.labels = labels

    async def label_registry(self):
        return [{"name": "mirror", "label_id": "mirror"},
                {"name": "mirror-rw", "label_id": "mirror_rw"}]

    async def entity_registry(self):
        return [{"entity_id": e, "labels": list(l)} for e, l in self.labels.items()]

    async def device_registry(self):
        return []


def labelled(states, labels):
    ha, broker = RegistryHA(states, labels), FakeBroker()
    return Bridge(Config(), ha, broker), ha, broker


def old_discovery(entity_id):
    """A config as 0.10.0 and earlier wrote it."""
    return json.dumps({"unique_id": "mirror_" + entity_id.replace(".", "_"),
                       "availability_topic": "mirror/link"})


# --- availability -----------------------------------------------------------
def test_every_entity_is_live_only_with_the_link_the_addon_and_its_source():
    for st, writable in ((BATTERY, False), (VALVE, True), (CANCEL, True)):
        eid = st["entity_id"]
        b, _ha, _broker = bridge([st], writable)
        payload = b._discovery(eid, st, classes.describe(eid, st, writable))
        assert "availability_topic" not in payload
        assert payload["availability_mode"] == "all"
        assert [a["topic"] for a in payload["availability"]] == [
            "mirror/link", ALIVE,
            "mirror/state/%s/available" % eid.replace(".", "/")]


def test_an_unavailable_source_is_unavailable_and_keeps_its_last_state():
    b, _ha, broker = bridge([BATTERY], writable=False)
    b.publish_entity("sensor.valve_battery", BATTERY)
    assert broker.sent["mirror/state/sensor/valve_battery/available"] == "1"
    b.publish_entity("sensor.valve_battery", dict(BATTERY, state="unavailable"))
    assert broker.sent["mirror/state/sensor/valve_battery/available"] == "0"
    # Never the word itself: a numeric sensor refuses it and logs an error.
    assert broker.sent["mirror/state/sensor/valve_battery/state"] == "87"
    b.publish_entity("sensor.valve_battery", dict(BATTERY, state="88"))
    assert broker.sent["mirror/state/sensor/valve_battery/available"] == "1"
    assert broker.sent["mirror/state/sensor/valve_battery/state"] == "88"


def test_an_unknown_source_is_unknown_not_unavailable():
    b, _ha, broker = bridge([BATTERY, VALVE], writable=False)
    b.publish_entity("sensor.valve_battery", dict(BATTERY, state="unknown"))
    assert broker.sent["mirror/state/sensor/valve_battery/available"] == "1"
    assert broker.sent["mirror/state/sensor/valve_battery/state"] == "None"
    # A thermostat's state topic is its mode, which takes "None" the same way.
    b.publish_entity("climate.valve", dict(VALVE, state="unknown"))
    assert broker.sent["mirror/state/climate/valve/state"] == "None"


def test_availability_is_published_when_it_changes_not_with_every_state():
    b, _ha, broker = bridge([BATTERY], writable=False)
    b.publish_entity("sensor.valve_battery", BATTERY)
    del broker.sent["mirror/state/sensor/valve_battery/available"]
    b.publish_entity("sensor.valve_battery", dict(BATTERY, state="86"))
    assert "mirror/state/sensor/valve_battery/available" not in broker.sent
    # A re-announce says everything again, whatever was said before.
    b.publish_entity("sensor.valve_battery", BATTERY, announce=True)
    assert broker.sent["mirror/state/sensor/valve_battery/available"] == "1"


def test_a_button_has_availability_and_no_state():
    b, _ha, broker = bridge([CANCEL])
    b.publish_entity("button.cancel", CANCEL)
    assert broker.sent["mirror/state/button/cancel/available"] == "1"
    assert "mirror/state/button/cancel/state" not in broker.sent
    b.publish_entity("button.cancel", dict(CANCEL, state="unavailable"))
    assert broker.sent["mirror/state/button/cancel/available"] == "0"


# --- retraction while running -----------------------------------------------
def test_taking_the_label_off_clears_every_topic_of_the_entity():
    b, ha, broker = labelled([BATTERY], {"sensor.valve_battery": ["mirror"]})
    run(b.refresh_members())
    assert "mirror/discovery/sensor/sensor_valve_battery/config" in broker.sent
    ha.labels["sensor.valve_battery"] = []
    run(b.refresh_members())
    assert broker.sent == {}
    assert set(broker.retracted) == {
        "mirror/discovery/sensor/sensor_valve_battery/config",
        "mirror/state/sensor/valve_battery/state",
        "mirror/state/sensor/valve_battery/available",
        "mirror/state/sensor/valve_battery/attributes"}


def test_the_writable_label_swaps_the_platform_and_retracts_the_old_one():
    b, ha, broker = labelled([SPEED], {"input_number.speed": ["mirror"]})
    run(b.refresh_members())
    assert "mirror/discovery/sensor/input_number_speed/config" in broker.sent
    ha.labels["input_number.speed"] = ["mirror", "mirror_rw"]
    run(b.refresh_members())
    assert "mirror/discovery/sensor/input_number_speed/config" not in broker.sent
    assert "mirror/discovery/number/input_number_speed/config" in broker.sent


def test_a_button_made_read_only_leaves_the_mirror():
    b, ha, broker = labelled([CANCEL], {"button.cancel": ["mirror", "mirror_rw"]})
    run(b.refresh_members())
    assert "mirror/discovery/button/button_cancel/config" in broker.sent
    ha.labels["button.cancel"] = ["mirror"]
    run(b.refresh_members())
    assert broker.sent == {}


def test_a_member_without_a_state_is_announced_by_its_first_one():
    b, _ha, broker = labelled([], {"sensor.valve_battery": ["mirror"]})
    run(b.refresh_members())
    assert broker.sent == {}
    run(b.on_state_changed({"data": {"entity_id": "sensor.valve_battery",
                                     "new_state": BATTERY}}))
    assert "mirror/discovery/sensor/sensor_valve_battery/config" in broker.sent
    assert broker.sent["mirror/state/sensor/valve_battery/state"] == "87"


# --- retraction of what an earlier run left ---------------------------------
def test_reconcile_retracts_what_is_no_longer_a_member_and_nothing_else():
    b, _ha, broker = labelled([BATTERY], {"sensor.valve_battery": ["mirror"]})
    run(b.refresh_members())
    mine = dict(broker.sent)
    foreign = "mirror/discovery/sensor/someone_else/config"
    gone = {"mirror/discovery/sensor/sensor_gone/config": old_discovery("sensor.gone"),
            "mirror/state/sensor/gone/state": "1",
            "mirror/state/sensor/gone/attributes": "{}"}
    b.reconcile({**mine, **gone, ALIVE: "0",
                 foreign: json.dumps({"unique_id": "other", "state_topic": "x"})})
    assert set(broker.retracted) == set(gone)


def test_reconcile_retracts_the_platform_a_member_had_before():
    b, _ha, broker = labelled(
        [SPEED, CANCEL], {"input_number.speed": ["mirror", "mirror_rw"],
                          "button.cancel": ["mirror"]})
    run(b.refresh_members())
    # Left by a run in which the helper was read-only and the button writable.
    left = {"mirror/discovery/sensor/input_number_speed/config":
                old_discovery("input_number.speed"),
            "mirror/discovery/button/button_cancel/config": old_discovery("button.cancel"),
            "mirror/state/button/cancel/state": "unknown"}
    b.reconcile({**broker.sent, **left})
    assert set(broker.retracted) == set(left)
    assert "mirror/discovery/number/input_number_speed/config" in broker.sent


def test_reconcile_leaves_a_member_that_has_no_state_yet():
    # After a restart an integration may load late: its entities are labelled
    # but not in the state machine. Retracting them would delete them from the
    # mirror, registry entry and all.
    b, _ha, broker = labelled([], {"climate.valve": ["mirror", "mirror_rw"]})
    run(b.refresh_members())
    b.reconcile({"mirror/discovery/climate/climate_valve/config": old_discovery("climate.valve"),
                 "mirror/state/climate/valve/state": "heat",
                 "mirror/state/climate/valve/temperature": "21"})
    assert broker.retracted == []
