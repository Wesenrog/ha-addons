import time

import pytest
from harness import LOCATION, MOTION, wait_for

from homely_bridge.api import HomelyAPI, HomelyAuthError


def test_full_flow(fake, running):
    bridge, mq = running
    hub = f"homely/{LOCATION}/state"
    state = wait_for(lambda: (s := mq.json(hub)) and s["live_updates"] == "ON" and s["last_poll_status"] == "success" and s)
    assert state["alarm"] == "disarmed"
    assert state["armed"] == "OFF"
    assert state["devices_offline"] == 1
    assert state["battery_problem"] == "ON"
    assert state["tamper"] == "OFF"
    assert mq.messages["homely/bridge/availability"] == "online"

    disc = mq.json(f"homeassistant/binary_sensor/homely_{MOTION}/alarm_alarm/config")
    assert disc["device_class"] == "motion"
    assert disc["state_topic"] == f"homely/{LOCATION}/{MOTION}/state"
    assert disc["device"]["suggested_area"] == "Entrance"
    assert disc["device"]["via_device"] == f"homely_{LOCATION}"
    assert mq.json(f"homeassistant/sensor/homely_{LOCATION}/alarm/config")["json_attributes_topic"] == f"homely/{LOCATION}/alarm"
    assert mq.json(f"homely/{LOCATION}/{MOTION}/state")["temperature_temperature"] == 20.3

    # Live alarm change
    fake.emit({"type": "alarm-state-changed", "data": {
        "locationId": LOCATION, "state": "ARMED_AWAY", "userName": "Kari", "timestamp": "2026-10-30T10:00:00Z"}})
    wait_for(lambda: mq.json(hub)["alarm"] == "armed_away")
    assert mq.json(hub)["armed"] == "ON"
    assert mq.json(f"homely/{LOCATION}/alarm")["changed_by"] == "Kari"

    # Live device change, including a state the device did not have before
    fake.emit({"type": "device-state-changed", "data": {"deviceId": MOTION, "changes": [
        {"feature": "alarm", "stateName": "alarm", "value": True, "lastUpdated": "2026-10-30T10:00:01Z"},
        {"feature": "humidity", "stateName": "humidity", "value": 41.5, "lastUpdated": "2026-10-30T10:00:01Z"},
    ]}})
    wait_for(lambda: mq.json(f"homely/{LOCATION}/{MOTION}/state")["alarm_alarm"] == "ON")
    assert mq.json(f"homely/{LOCATION}/{MOTION}/state")["humidity_humidity"] == 41.5
    wait_for(lambda: mq.json(f"homeassistant/sensor/homely_{MOTION}/humidity_humidity/config"))

    # Websocket drop: reported, then reconnected
    fake.kick_websockets()
    wait_for(lambda: mq.json(hub)["live_updates"] == "OFF", timeout=5)
    wait_for(lambda: mq.json(hub)["live_updates"] == "ON", timeout=10)
    assert fake.ws_rejected == 0


def test_rate_limited_poll_recovers(fake, running):
    fake.home_errors.append((429, {"Retry-After": "1"}))
    fake.home_errors.append((439, {}))
    bridge, mq = running
    hub = f"homely/{LOCATION}/state"
    wait_for(lambda: (s := mq.json(hub)) and s["last_poll_status"] == "failed_429")
    # The hub entities exist even without device data
    assert mq.json(f"homeassistant/sensor/homely_{LOCATION}/alarm/config")
    wait_for(lambda: mq.json(hub)["last_poll_status"] == "success", timeout=10)
    assert fake.calls.count("home") == 3
    assert mq.json(hub)["alarm"] == "disarmed"


def test_home_reconnect_republishes(fake, running):
    bridge, mq = running
    topic = f"homeassistant/binary_sensor/homely_{MOTION}/alarm_alarm/config"
    wait_for(lambda: mq.messages.get(topic))
    mq.messages.pop(topic)
    mq.client.publish("homeassistant/status", "online")
    wait_for(lambda: mq.messages.get(topic))


def test_token_refresh_and_relogin(fake):
    fake.expires_in = 6  # refreshed once less than 5 s remain
    api = HomelyAPI(fake.username, fake.password, base_url=fake.api_url)
    first = api.token()
    assert api.token() == first
    time.sleep(1.2)
    second = api.token()
    assert second != first and fake.calls[-1] == "refresh"
    # A rejected refresh token falls back to a full login
    fake.refresh_tokens.clear()
    time.sleep(1.2)
    third = api.token()
    assert third != second and fake.calls[-2:] == ["refresh", "login"]
    assert api.get_locations()[0]["locationId"] == LOCATION


def test_expired_access_token_is_replaced(fake):
    api = HomelyAPI(fake.username, fake.password, base_url=fake.api_url)
    api.token()
    fake.valid_tokens.clear()
    assert api.get_home(LOCATION)["locationId"] == LOCATION
    assert fake.calls.count("login") == 2


def test_bad_credentials(fake):
    api = HomelyAPI(fake.username, "wrong", base_url=fake.api_url)
    with pytest.raises(HomelyAuthError):
        api.token()


def test_publishes_only_under_own_prefixes(fake, running):
    bridge, mq = running
    wait_for(lambda: mq.messages.get(f"homely/{LOCATION}/availability") == "online", timeout=10)
    fake.emit({"type": "alarm-state-changed", "data": {"state": "ARMED_NIGHT"}})
    wait_for(lambda: mq.json(f"homely/{LOCATION}/state")["alarm"] == "armed_night")
    topics = {t for _, t, _ in mq.history}
    assert topics and all(t.startswith(("homely/", "homeassistant/")) for t in topics), topics
