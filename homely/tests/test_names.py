import copy
import json
from pathlib import Path

import pytest
from test_snapshot import RecordingPublisher

from homely_bridge.bridge import Bridge
from homely_bridge.config import Config
from homely_bridge.model import NAMES, slugify

BASELINE = json.loads((Path(__file__).parent / "discovery_0.1.0.json").read_text())


def discovery(home, language):
    """All discovery payloads for the fixture home, as 0.1.0 produced them."""
    pub = RecordingPublisher()
    bridge = Bridge(Config(username="u", password="p", language=language), api=None, publisher=pub)
    bridge.location = {"name": home["name"], "locationId": home["locationId"]}
    bridge.location_id = home["locationId"]
    bridge._publish_hub_discovery()
    bridge.apply_snapshot(home)
    bridge.handle_event({"type": "device-state-changed", "data": {"deviceId": home["devices"][0]["id"], "changes": [
        {"feature": "humidity", "stateName": "humidity", "value": 40}]}})
    return {t: p for t, p in pub.published if t.endswith("/config") and p}


@pytest.mark.parametrize("language", ["nb", "en"])
def test_unique_ids_unchanged_since_0_1_0(home_payload, language):
    payloads = discovery(home_payload, language)
    assert {t: p["unique_id"] for t, p in payloads.items()} == {t: b["unique_id"] for t, b in BASELINE.items()}


def test_english_names_unchanged_since_0_1_0(home_payload):
    payloads = discovery(home_payload, "en")
    assert {t: (p["name"], p["device"].get("model")) for t, p in payloads.items()} == {
        t: (b["name"], b["model"]) for t, b in BASELINE.items()
    }


def test_norwegian_names(home_payload):
    payloads = discovery(home_payload, "nb")
    nb_names = {n["nb"] for n in NAMES.values()}
    assert all(p["name"] in nb_names for p in payloads.values())
    hub, motion = home_payload["locationId"], home_payload["devices"][0]["id"]
    assert payloads[f"homeassistant/sensor/homely_{hub}/devices_offline/config"]["name"] == "Enheter frakoblet"
    assert payloads[f"homeassistant/sensor/homely_{hub}/alarm/config"]["device"]["model"] == "Alarmsystem"
    assert payloads[f"homeassistant/binary_sensor/homely_{motion}/alarm_alarm/config"]["name"] == "Bevegelse"
    assert payloads[f"homeassistant/sensor/homely_{motion}/humidity_humidity/config"]["name"] == "Luftfuktighet"
    door = home_payload["devices"][3]["id"]
    assert payloads[f"homeassistant/binary_sensor/homely_{door}/alarm_alarm/config"]["name"] == "Kontakt"


def test_every_name_has_both_languages():
    assert all(set(n) == {"nb", "en"} and all(n.values()) for n in NAMES.values())


def test_entity_ids_do_not_depend_on_language(home_payload):
    nb = discovery(home_payload, "nb")
    en = discovery(home_payload, "en")
    ids = {t: p["default_entity_id"] for t, p in nb.items()}
    assert ids == {t: p["default_entity_id"] for t, p in en.items()}
    hub, motion = home_payload["locationId"], home_payload["devices"][0]["id"]
    assert ids[f"homeassistant/sensor/homely_{hub}/alarm/config"] == "sensor.homely_alarm"
    assert ids[f"homeassistant/binary_sensor/homely_{hub}/live_updates/config"] == "binary_sensor.homely_direkteoppdatering"
    assert ids[f"homeassistant/binary_sensor/homely_{hub}/armed/config"] == "binary_sensor.homely_aktivert"
    assert ids[f"homeassistant/binary_sensor/homely_{motion}/alarm_alarm/config"] == "binary_sensor.homely_hall_motion_bevegelse"
    assert ids[f"homeassistant/sensor/homely_{motion}/temperature_temperature/config"] == "sensor.homely_hall_motion_temperatur"
    assert ids[f"homeassistant/sensor/homely_{motion}/diagnostic_networklinkstrength/config"] == (
        "sensor.homely_hall_motion_signalstyrke"
    )
    # Every id has the component as its domain
    assert all(p["default_entity_id"].split(".")[0] == t.split("/")[1] for t, p in nb.items())


def test_norwegian_device_name_slug(home_payload):
    home = copy.deepcopy(home_payload)
    door = home["devices"][3]
    door["name"] = "Garasjedør bak"
    payloads = discovery(home, "nb")
    payload = payloads[f"homeassistant/binary_sensor/homely_{door['id']}/alarm_alarm/config"]
    assert payload["default_entity_id"] == "binary_sensor.homely_garasjedor_bak_kontakt"
    assert payload["device"]["name"] == "Garasjedør bak"
    assert payload["name"] == "Kontakt"


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Garasjedør", "garasjedor"),
        ("Garasjedør bak", "garasjedor_bak"),
        ("Bad 1. etg", "bad_1_etg"),
        ("Kjøkken/Spisestue", "kjokken_spisestue"),
        ("Ærlig Åpen Øst", "aerlig_apen_ost"),
        ("  Gang - inngang  ", "gang_inngang"),
        ("Café", "cafe"),
        ("Straße", "strasse"),
        ("Rom #2 (vest)", "rom_2_vest"),
        ("ÆØÅ", "aeoa"),
        ("!!!", "unknown"),
    ],
)
def test_slugify(name, slug):
    assert slugify(name) == slug
