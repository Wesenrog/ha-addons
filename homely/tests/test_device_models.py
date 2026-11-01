"""The models a real installation reports, with made-up names and ids (home_models.json)."""

import copy
import json
import logging
from collections import Counter
from pathlib import Path

import pytest
from test_snapshot import RecordingPublisher

from homely_bridge import model
from homely_bridge.bridge import Bridge
from homely_bridge.config import Config
from homely_bridge.model import device_entities, device_state_payload, entity_id_suffix

REAL = json.loads((Path(__file__).parent / "home_models.json").read_text())


@pytest.fixture
def real():
    return copy.deepcopy(REAL)


def devices_of(home, model_name):
    return [d for d in home["devices"] if d["modelName"] == model_name]


def entities(device):
    """{spec key: (component, device class, suffix, enabled)} for a device."""
    return {
        s.key: (s.component, s.device_class, entity_id_suffix(s), s.enabled_by_default)
        for s, _ in device_entities(device)
    }


def discovery(home, language="nb"):
    pub = RecordingPublisher()
    bridge = Bridge(Config(username="u", password="p", language=language), api=None, publisher=pub)
    bridge.location = {"name": home["name"], "locationId": home["locationId"]}
    bridge.location_id = home["locationId"]
    bridge._publish_hub_discovery()
    bridge.apply_snapshot(home)
    return bridge, pub, {t: p for t, p in pub.published if t.endswith("/config") and p}


def test_fixture_has_every_model(real):
    assert Counter(d["modelName"] for d in real["devices"]) == {
        "Intelligent Smoke Alarm": 3,
        "Entry Sensor": 1,
        "Alarm Entry Sensor 2": 2,
        "Alarm Motion Sensor 2": 3,
        "Yale Doorman L3": 1,
        "ELKO Super TR": 1,
        "Intelligent Heat Alarm": 1,
    }


@pytest.mark.parametrize("model_name", ["Entry Sensor", "Alarm Entry Sensor 2"])
def test_entry_sensors_are_contacts(real, model_name):
    for device in devices_of(real, model_name):
        assert entities(device)["alarm_alarm"] == ("binary_sensor", "opening", "kontakt", True)


def test_motion_sensors(real):
    for device in devices_of(real, "Alarm Motion Sensor 2"):
        assert entities(device)["alarm_alarm"] == ("binary_sensor", "motion", "bevegelse", True)


def test_heat_and_smoke_alarms(real):
    (heat,) = devices_of(real, "Intelligent Heat Alarm")
    assert entities(heat)["alarm_fire"] == ("binary_sensor", "heat", "brann", True)
    for smoke in devices_of(real, "Intelligent Smoke Alarm"):
        assert entities(smoke)["alarm_fire"] == ("binary_sensor", "smoke", "brann", True)


def test_locks(real):
    for lock in devices_of(real, "Yale Doorman L3"):
        found = entities(lock)
        assert found == {
            "online": ("binary_sensor", "connectivity", "tilkoblet", True),
            "report_locked": ("binary_sensor", "lock", "las", True),
            "report_doorclosed": ("binary_sensor", "door", "dor", True),
            "report_lowbat": ("binary_sensor", "battery", "las_lavt_batteri", True),
            "report_broken": ("binary_sensor", "problem", "las_feil", True),
            "report_errorcode": ("sensor", None, "las_feilkode", False),
        }
        # Exactly one lock entity, although lock.state reports the same thing
        assert [k for k, v in found.items() if v[1] == "lock"] == ["report_locked"]


def test_lock_states_are_inverted_for_home_assistant(real):
    lock = devices_of(real, "Yale Doorman L3")[0]
    report = lock["features"]["report"]["states"]
    report["locked"]["value"] = True
    report["doorclosed"]["value"] = True
    payload = device_state_payload(lock)
    assert payload["report_locked"] == "OFF"  # HA lock: on = unlocked
    assert payload["report_doorclosed"] == "OFF"  # HA door: on = open
    report["locked"]["value"] = False
    report["doorclosed"]["value"] = False
    payload = device_state_payload(lock)
    assert payload["report_locked"] == "ON"
    assert payload["report_doorclosed"] == "ON"
    assert payload["report_errorcode"] == "Success"


def test_lock_battery_counts_for_the_hub(real):
    bridge, pub, _ = discovery(real)
    hub_state = f"homely/{real['locationId']}/state"
    assert pub.last(hub_state)["battery_problem"] == "OFF"
    devices_of(real, "Yale Doorman L3")[0]["features"]["report"]["states"]["lowbat"]["value"] = True
    bridge.apply_snapshot(real)
    assert pub.last(hub_state)["battery_problem"] == "ON"


@pytest.mark.parametrize("language", ["nb", "en"])
def test_fixture_ids_are_unique(real, language):
    _, _, payloads = discovery(real, language)
    ids = Counter(p["default_entity_id"] for p in payloads.values())
    assert [i for i, n in ids.items() if n > 1] == []


def test_fixture_examples(real):
    _, _, payloads = discovery(real)
    ids = {p["default_entity_id"]: p for p in payloads.values()}
    contact = ids["binary_sensor.homely_kontakt_a_kontakt"]
    assert contact["device_class"] == "opening"
    assert contact["name"] == "Kontakt"
    assert ids["binary_sensor.homely_vindu_b_kontakt"]["device_class"] == "opening"
    assert ids["binary_sensor.homely_las_a_las"]["device_class"] == "lock"
    assert ids["binary_sensor.homely_las_a_dor"]["name"] == "Dør"
    assert ids["sensor.homely_las_a_las_feilkode"]["enabled_by_default"] is False
    assert ids["binary_sensor.homely_varmevarsler_brann"]["device_class"] == "heat"
    assert "binary_sensor.homely_bevegelse_c_bevegelse" in ids
    assert "sensor.homely_gulvvarme_termostat_temperatur" in ids


def test_unknown_model_is_generic_and_logged_once(caplog, monkeypatch):
    monkeypatch.setattr(model, "_unknown_alarm_models", set())
    device = {"modelName": "Brand New Sensor", "features": {"alarm": {"states": {"alarm": {"value": False}}}}}
    with caplog.at_level(logging.WARNING, logger="homely_bridge.model"):
        for _ in range(3):
            spec = {s.key: s for s, _ in device_entities(device)}["alarm_alarm"]
    assert (spec.device_class, entity_id_suffix(spec)) == ("safety", "alarm")
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and "Brand New Sensor" in lines[0]


def test_known_models_need_no_words_in_the_name():
    # Classified by the table, so a name without "door" or "window" still works,
    # and a name that merely contains "door" no longer guesses
    entry = {"modelName": "Entry Sensor", "features": {"alarm": {"states": {"alarm": {"value": True}}}}}
    assert {s.key: s for s, _ in device_entities(entry)}["alarm_alarm"].device_class == "opening"
    other = {"modelName": "Doorbell Thing", "features": {"alarm": {"states": {"alarm": {"value": True}}}}}
    assert {s.key: s for s, _ in device_entities(other)}["alarm_alarm"].device_class == "safety"
