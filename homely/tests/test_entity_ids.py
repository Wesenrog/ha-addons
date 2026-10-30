"""Readable, frozen entity id suffixes."""

import copy
import re
from collections import Counter

import pytest
from test_names import discovery

from homely_bridge.bridge import HUB_SPECS
from homely_bridge.model import (
    ENTITY_ID_SUFFIXES,
    EntitySpec,
    device_entities,
    entity_id_suffix,
    entity_id_suffix_entry,
)

# The suffix table. Entity ids are fixed in Home
# Assistant's registry once discovered: this must never change, only grow.
FROZEN = {
    "alarm": "alarm",
    "armed": "aktivert",
    "triggered": "utlost",
    "battery_problem": "batteriproblem",
    "tamper": "sabotasje",
    "devices_offline": "enheter_frakoblet",
    "live_updates": "direkteoppdatering",
    "last_poll": "siste_henting",
    "last_poll_status": "status_siste_henting",
    "alarm_alarm/motion": "bevegelse",
    "alarm_alarm/contact": "kontakt",
    "alarm_alarm/alarm": "alarm",
    "alarm_tamper": "sabotasje",
    "alarm_fire": "brann",
    "alarm_flood": "vannlekkasje",
    "temperature_temperature": "temperatur",
    "thermostat_localtemperature": "termostat_temperatur",
    "thermostat_occupiedheatingsetpoint": "settpunkt",
    "humidity_humidity": "luftfuktighet",
    "battery_low": "lavt_batteri",
    "battery_defect": "batterifeil",
    "battery_voltage": "batterispenning",
    "lock_lowbat": "las_lavt_batteri",
    "lock_low_battery": "las_lavt_batteri",
    "lock_locked": "las",
    "lock_jammed": "fastkjort",
    "diagnostic_networklinkstrength": "signalstyrke",
    "diagnostic_networklinkaddress": "overordnet_node",
    "metering_summationdelivered": "energi_importert",
    "metering_summationreceived": "energi_eksportert",
    "metering_demand": "effekt",
    "metering_check": "malerkontroll",
    "online": "tilkoblet",
    # 0.4.1
    "report_locked": "las",
    "report_doorclosed": "dor",
    "report_lowbat": "las_lavt_batteri",
    "report_broken": "las_feil",
    "report_errorcode": "las_feilkode",
}

# Every feature/state the add-on supports today, all on one device
ALL_FEATURES = {
    "alarm": ["alarm", "tamper", "fire", "flood"],
    "temperature": ["temperature"],
    "humidity": ["humidity"],
    "battery": ["low", "defect", "voltage"],
    "diagnostic": ["networklinkstrength", "networklinkaddress"],
    "metering": ["summationdelivered", "summationreceived", "demand", "check"],
    "thermostat": ["LocalTemperature", "OccupiedHeatingSetpoint"],
    "report": ["locked", "doorclosed", "lowbat", "Broken", "errorcode"],
}
MODELS = ["Motion Sensor Mini", "Alarm Motion Sensor 2", "Window Sensor", "Entry Sensor", "Alarm Entry Sensor 2",
          "Intelligent Smoke Alarm", "Intelligent Heat Alarm", "Flood Alarm", "ELKO Super TR",
          "EMI Norwegian HAN", "Yale Doorman L3", "Some Future Sensor"]


def all_features_device(model, **extra_states):
    features = {f: {"states": {s: {"value": 1} for s in states}} for f, states in ALL_FEATURES.items()}
    for feature, state in extra_states.items():
        features[feature]["states"][state] = {"value": 1}
    return {"id": "x", "name": "Alt", "modelName": model, "online": True, "features": features}


def all_specs():
    specs = list(HUB_SPECS)
    for model in MODELS:
        specs += [spec for spec, _ in device_entities(all_features_device(model))]
    return specs


def test_table_is_frozen():
    assert ENTITY_ID_SUFFIXES == FROZEN


def test_suffixes_are_slugs():
    assert all(re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", s) for s in ENTITY_ID_SUFFIXES.values())


def test_every_spec_today_has_an_entry():
    missing = {(s.key, s.name_id) for s in all_specs() if entity_id_suffix_entry(s) is None}
    assert not missing


def test_unknown_spec_falls_back_to_raw_key():
    spec = EntitySpec("newfeature_newstate", "sensor", "temperature", lambda v: v)
    assert entity_id_suffix(spec) == "newfeature_newstate"


@pytest.mark.parametrize("model", MODELS)
def test_no_duplicate_ids_on_a_device_with_every_feature(model):
    suffixes = [(s.component, entity_id_suffix(s)) for s, _ in device_entities(all_features_device(model))]
    assert [k for k, n in Counter(suffixes).items() if n > 1] == []


def test_no_duplicate_ids_on_the_hub():
    suffixes = [(s.component, entity_id_suffix(s)) for s in HUB_SPECS]
    assert len(suffixes) == len(set(suffixes))


@pytest.mark.parametrize("language", ["nb", "en"])
def test_no_duplicate_ids_in_fixture(home_payload, language):
    ids = Counter(p["default_entity_id"] for p in discovery(home_payload, language).values())
    assert [i for i, n in ids.items() if n > 1] == []


def test_examples(home_payload):
    home = copy.deepcopy(home_payload)
    door = home["devices"][3]
    door["name"] = "Garasjedør bak"
    payloads = discovery(home, "en")
    hub = home["locationId"]
    ids = {p["default_entity_id"] for p in payloads.values()}
    assert {
        "binary_sensor.homely_garasjedor_bak_kontakt",
        "binary_sensor.homely_garasjedor_bak_lavt_batteri",
        "binary_sensor.homely_garasjedor_bak_tilkoblet",
        "sensor.homely_alarm",
        "binary_sensor.homely_aktivert",
        "binary_sensor.homely_utlost",
        "sensor.homely_status_siste_henting",
        "sensor.homely_bathroom_floor_termostat_temperatur",
        "sensor.homely_bathroom_floor_settpunkt",
        "sensor.homely_han_plug_energi_importert",
        "sensor.homely_han_plug_effekt",
    } <= ids
    assert payloads[f"homeassistant/sensor/homely_{hub}/alarm/config"]["default_entity_id"] == "sensor.homely_alarm"
