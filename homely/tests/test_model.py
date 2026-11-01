import pytest

from homely_bridge.model import device_entities, device_state_payload, normalize_alarm_state


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DISARMED", "disarmed"),
        ("ARMED_AWAY", "armed_away"),
        ("ARMED_NIGHT", "armed_night"),
        ("ARMED_STAY", "armed_home"),
        ("ARMED_PARTLY", "armed_home"),
        ("ARM_NIGHT_PENDING", "arming"),
        ("ARMED_AWAY_PENDING", "arming"),
        ("ALARM_PENDING", "pending"),
        ("ALARM_STAY_PENDING", "pending"),
        ("BREACHED", "triggered"),
        ("ARM_SOMETHING_NEW_PENDING", "arming"),
        ("WHATEVER", "unknown"),
        (None, "unknown"),
    ],
)
def test_alarm_states(raw, expected):
    assert normalize_alarm_state(raw) == expected


def _by_key(device):
    return {spec.key: spec for spec, _ in device_entities(device)}


def test_motion_sensor(home_payload):
    device = home_payload["devices"][0]
    specs = _by_key(device)
    assert specs["alarm_alarm"].device_class == "motion"
    assert specs["alarm_tamper"].entity_category == "diagnostic"
    assert specs["diagnostic_networklinkaddress"].enabled_by_default is False
    assert device_state_payload(device) == {
        "online": "ON",
        "alarm_alarm": "OFF",
        "alarm_tamper": "OFF",
        "temperature_temperature": 20.3,
        "battery_low": "OFF",
        "battery_defect": "OFF",
        "battery_voltage": 2.9,
        "diagnostic_networklinkstrength": 87,
        "diagnostic_networklinkaddress": "0015BC002C102E91",
    }


def test_thermostat_scaled_and_unknown_states_ignored(home_payload):
    payload = device_state_payload(home_payload["devices"][1])
    assert payload == {
        "online": "ON",
        "thermostat_localtemperature": 23.8,
        "thermostat_occupiedheatingsetpoint": 28.0,
    }


def test_han_plug(home_payload):
    device = home_payload["devices"][2]
    payload = device_state_payload(device)
    assert payload["metering_summationdelivered"] == 769.75
    assert payload["metering_demand"] == 104
    assert payload["online"] == "OFF"
    assert _by_key(device)["metering_summationdelivered"].state_class == "total_increasing"


def test_window_sensor_is_opening():
    device = {"modelName": "Window Sensor", "features": {"alarm": {"states": {"alarm": {"value": True}}}}}
    assert _by_key(device)["alarm_alarm"].device_class == "opening"
    assert device_state_payload(device) == {"alarm_alarm": "ON"}


def test_lock_is_inverted_for_home_assistant():
    device = {"modelName": "Yale Doorman L3", "features": {"report": {"states": {"locked": {"value": True}}}}}
    assert device_state_payload(device) == {"report_locked": "OFF"}


def test_flood_and_fire():
    device = {"modelName": "Flood Alarm", "features": {"alarm": {"states": {
        "flood": {"value": True}, "fire": {"value": False}}}}}
    specs = _by_key(device)
    assert specs["alarm_flood"].device_class == "moisture"
    assert specs["alarm_fire"].device_class == "smoke"
