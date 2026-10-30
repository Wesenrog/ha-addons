"""Mapping from Homely API data to Home Assistant entities.

The Homely `/home` payload describes each device as
`features.<feature>.states.<state>.value`. Each known (feature, state) pair
is mapped to one Home Assistant entity here. Unknown pairs are ignored.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

# Homely alarm states -> normalized state published to MQTT.
# The official API document (Oct 2025) and observed traffic disagree on some
# spellings, so both variants are listed.
ALARM_STATES = {
    "DISARMED": "disarmed",
    "ARMED_AWAY": "armed_away",
    "ARMED_NIGHT": "armed_night",
    "ARMED_STAY": "armed_home",
    "ARMED_PARTLY": "armed_home",
    "ARM_PENDING": "arming",
    "ARM_STAY_PENDING": "arming",
    "ARM_NIGHT_PENDING": "arming",
    "ARM_AWAY_PENDING": "arming",
    "ARMED_NIGHT_PENDING": "arming",
    "ARMED_AWAY_PENDING": "arming",
    "ARMED_STAY_PENDING": "arming",
    "ALARM_PENDING": "pending",
    "ALARM_STAY_PENDING": "pending",
    "BREACHED": "triggered",
    "TRIGGERED": "triggered",
    "ALARM": "triggered",
}


def normalize_alarm_state(raw: Any) -> str:
    """Return a Home Assistant style alarm state for a Homely alarm state."""
    if not isinstance(raw, str) or not raw:
        return "unknown"
    state = ALARM_STATES.get(raw.upper())
    if state is not None:
        return state
    # Fall back on the shape of the name for spellings we have not seen yet
    upper = raw.upper()
    if upper.endswith("_PENDING"):
        return "pending" if upper.startswith("ALARM") else "arming"
    _LOGGER.warning("Unknown Homely alarm state %r", raw)
    return "unknown"


# Entity names per language. Entity ids do not depend on these
# (see default_entity_id in mqtt.py), so wording can change freely.
LANGUAGES = ("nb", "en")
NAMES: dict[str, dict[str, str]] = {
    # Hub (the home)
    "alarm": {"en": "Alarm", "nb": "Alarm"},
    "armed": {"en": "Armed", "nb": "Aktivert"},
    "triggered": {"en": "Triggered", "nb": "Utløst"},
    "battery_problem": {"en": "Battery problem", "nb": "Batteriproblem"},
    "devices_offline": {"en": "Offline devices", "nb": "Enheter frakoblet"},
    "live_updates": {"en": "Live updates", "nb": "Direkteoppdatering"},
    "last_poll": {"en": "Last poll", "nb": "Siste henting"},
    "last_poll_status": {"en": "Last poll status", "nb": "Status siste henting"},
    "hub_model": {"en": "Alarm system", "nb": "Alarmsystem"},
    # Devices
    "motion": {"en": "Motion", "nb": "Bevegelse"},
    "contact": {"en": "Contact", "nb": "Kontakt"},
    "tamper": {"en": "Tamper", "nb": "Sabotasje"},
    "fire": {"en": "Fire", "nb": "Brann"},
    "flood": {"en": "Flood", "nb": "Vannlekkasje"},
    "temperature": {"en": "Temperature", "nb": "Temperatur"},
    "humidity": {"en": "Humidity", "nb": "Luftfuktighet"},
    "battery_low": {"en": "Battery low", "nb": "Lavt batteri"},
    "battery_defect": {"en": "Battery defect", "nb": "Batterifeil"},
    "battery_voltage": {"en": "Battery voltage", "nb": "Batterispenning"},
    "link_quality": {"en": "Link quality", "nb": "Signalstyrke"},
    "parent_address": {"en": "Parent address", "nb": "Overordnet node"},
    "energy_imported": {"en": "Energy imported", "nb": "Energi importert"},
    "energy_exported": {"en": "Energy exported", "nb": "Energi eksportert"},
    "power": {"en": "Power", "nb": "Effekt"},
    "metering_check": {"en": "Metering check", "nb": "Målerkontroll"},
    "heating_setpoint": {"en": "Heating setpoint", "nb": "Settpunkt"},
    "lock": {"en": "Lock", "nb": "Lås"},
    "online": {"en": "Online", "nb": "Tilkoblet"},
    "door": {"en": "Door", "nb": "Dør"},
    "fault": {"en": "Fault", "nb": "Feil"},
    "error_code": {"en": "Error code", "nb": "Feilkode"},
}


# Entity id suffixes, used only for default_entity_id. FROZEN: an entity id
# is fixed in Home Assistant's registry the first time an entity is
# discovered, and automations refer to it. Never change or remove an entry;
# only add entries for new specs. Separate from NAMES on purpose, so it
# follows neither the language option nor changes in wording.
# Keyed by spec key, or "<key>/<name_id>" where one key has several meanings.
ENTITY_ID_SUFFIXES: dict[str, str] = {
    # Hub (the home)
    "alarm": "alarm",
    "armed": "aktivert",
    "triggered": "utlost",
    "battery_problem": "batteriproblem",
    "tamper": "sabotasje",
    "devices_offline": "enheter_frakoblet",
    "live_updates": "direkteoppdatering",
    "last_poll": "siste_henting",
    "last_poll_status": "status_siste_henting",
    # Devices
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
    # Added in 0.4.1, from the payloads of real Yale Doorman locks
    "report_locked": "las",
    "report_doorclosed": "dor",
    "report_lowbat": "las_lavt_batteri",
    "report_broken": "las_feil",
    "report_errorcode": "las_feilkode",
}


def entity_id_suffix_entry(spec: "EntitySpec") -> str | None:
    """The table entry for a spec, or None if it has none."""
    return ENTITY_ID_SUFFIXES.get(f"{spec.key}/{spec.name_id}") or ENTITY_ID_SUFFIXES.get(spec.key)


def entity_id_suffix(spec: "EntitySpec") -> str:
    """Suffix for a spec's default_entity_id; a spec not in the table uses its raw key."""
    return entity_id_suffix_entry(spec) or spec.key


def entity_name(name_id: str, language: str) -> str:
    names = NAMES[name_id]
    return names.get(language) or names["en"]


# Letters that Unicode decomposition does not reduce to ASCII. These match
# the transliteration Home Assistant's slugify uses (python-slugify).
_TRANSLITERATE = str.maketrans({
    "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "å": "a", "Å": "A",
    "ß": "ss", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "þ": "th", "Þ": "TH",
    "ð": "d", "Ð": "D", "œ": "oe", "Œ": "OE",
})


def slugify(text: str) -> str:
    """Slugify like Home Assistant's util.slugify: 'Garasjedør bak' -> 'garasjedor_bak'."""
    text = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATE))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return slug or "unknown"


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "on", "1", "yes"):
            return True
        if lowered in ("false", "off", "0", "no"):
            return False
    return None


def on_off(value: Any) -> str | None:
    b = to_bool(value)
    if b is None:
        return None
    return "ON" if b else "OFF"


def inverted_on_off(value: Any) -> str | None:
    b = to_bool(value)
    if b is None:
        return None
    return "OFF" if b else "ON"


def number(scale: float = 1.0, digits: int | None = None) -> Callable[[Any], float | None]:
    def convert(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            result = float(value) / scale
        except (TypeError, ValueError):
            return None
        if digits is not None:
            result = round(result, digits)
        return result

    return convert


def text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


@dataclass(frozen=True)
class EntitySpec:
    """One Home Assistant entity derived from a Homely device state."""

    key: str
    component: str  # "sensor" or "binary_sensor"
    name_id: str  # key into NAMES
    convert: Callable[[Any], Any]
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    enabled_by_default: bool = True
    icon: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# What alarm.alarm means, by model. The reported features can't tell a motion
# sensor from a contact, so known models are listed here; any other model
# gets the generic alarm. A contact is "opening", not "door": the sensor
# can't tell a door from a window.
ALARM_MODELS: dict[str, tuple[str, str]] = {
    # lowercase model name: (device class, name id)
    "motion sensor mini": ("motion", "motion"),
    "alarm motion sensor 2": ("motion", "motion"),
    "window sensor": ("opening", "contact"),
    "entry sensor": ("opening", "contact"),
    "alarm entry sensor 2": ("opening", "contact"),
}
GENERIC_ALARM = ("safety", "alarm")
# Models whose alarm.fire is a heat detector rather than a smoke detector
HEAT_ALARM_MODELS = {"intelligent heat alarm"}

_unknown_alarm_models: set[str] = set()


def _alarm_device_class(model_name: str) -> tuple[str, str]:
    known = ALARM_MODELS.get(model_name.strip().lower())
    if known is not None:
        return known
    if model_name not in _unknown_alarm_models:
        _unknown_alarm_models.add(model_name)
        _LOGGER.warning(
            "Unknown model %r reports an alarm state; it is shown as a generic alarm. "
            "Please report the model name so it can be added",
            model_name,
        )
    return GENERIC_ALARM


def entity_spec(model_name: str, feature: str, state: str) -> EntitySpec | None:
    """Return the entity for a feature/state pair, or None if unsupported."""
    key = f"{feature}_{state}".lower()
    f, s = feature.lower(), state.lower()

    if f == "alarm":
        if s == "alarm":
            device_class, name = _alarm_device_class(model_name)
            return EntitySpec(key, "binary_sensor", name, on_off, device_class)
        if s == "tamper":
            return EntitySpec(key, "binary_sensor", "tamper", on_off, "tamper", entity_category="diagnostic")
        if s == "fire":
            heat = model_name.strip().lower() in HEAT_ALARM_MODELS
            return EntitySpec(key, "binary_sensor", "fire", on_off, "heat" if heat else "smoke")
        if s == "flood":
            return EntitySpec(key, "binary_sensor", "flood", on_off, "moisture")
        return None

    if f == "temperature" and s == "temperature":
        return EntitySpec(key, "sensor", "temperature", number(digits=1), "temperature", "°C", "measurement")

    if f == "humidity" and s == "humidity":
        return EntitySpec(key, "sensor", "humidity", number(digits=1), "humidity", "%", "measurement")

    if f == "battery":
        if s == "low":
            return EntitySpec(key, "binary_sensor", "battery_low", on_off, "battery")
        if s == "defect":
            return EntitySpec(key, "binary_sensor", "battery_defect", on_off, "problem", entity_category="diagnostic")
        if s == "voltage":
            return EntitySpec(
                key, "sensor", "battery_voltage", number(digits=2), "voltage", "V", "measurement",
                entity_category="diagnostic",
            )
        return None

    if f == "diagnostic":
        if s == "networklinkstrength":
            return EntitySpec(
                key, "sensor", "link_quality", number(digits=0), None, "%", "measurement",
                entity_category="diagnostic", icon="mdi:signal",
            )
        if s == "networklinkaddress":
            return EntitySpec(
                key, "sensor", "parent_address", text, entity_category="diagnostic",
                enabled_by_default=False, icon="mdi:lan",
            )
        return None

    if f == "metering":
        # summationdelivered/received are in Wh, demand in W
        if s == "summationdelivered":
            return EntitySpec(key, "sensor", "energy_imported", number(1000, 3), "energy", "kWh", "total_increasing")
        if s == "summationreceived":
            return EntitySpec(key, "sensor", "energy_exported", number(1000, 3), "energy", "kWh", "total_increasing")
        if s == "demand":
            return EntitySpec(key, "sensor", "power", number(digits=0), "power", "W", "measurement")
        if s == "check":
            return EntitySpec(key, "binary_sensor", "metering_check", on_off, "problem", entity_category="diagnostic")
        return None

    if f == "thermostat":
        # Zigbee thermostat values are in 1/100 °C
        if s == "localtemperature":
            return EntitySpec(key, "sensor", "temperature", number(100, 1), "temperature", "°C", "measurement")
        if s == "occupiedheatingsetpoint":
            return EntitySpec(key, "sensor", "heating_setpoint", number(100, 1), "temperature", "°C", "measurement")
        return None

    if f == "report":
        # Yale Doorman locks. report.locked is the only lock source; the
        # duplicate lock.state is not mapped, so a lock has one lock entity.
        if s == "locked":
            # Home Assistant's lock device class is ON when unlocked
            return EntitySpec(key, "binary_sensor", "lock", inverted_on_off, "lock")
        if s == "doorclosed":
            # Home Assistant's door device class is ON when open
            return EntitySpec(key, "binary_sensor", "door", inverted_on_off, "door")
        if s == "lowbat":
            return EntitySpec(key, "binary_sensor", "battery_low", on_off, "battery")
        if s == "broken":
            return EntitySpec(key, "binary_sensor", "fault", on_off, "problem")
        if s == "errorcode":
            return EntitySpec(
                key, "sensor", "error_code", text, entity_category="diagnostic",
                enabled_by_default=False, icon="mdi:alert-circle-outline",
            )
        return None

    return None


ONLINE_SPEC = EntitySpec("online", "binary_sensor", "online", on_off, "connectivity", entity_category="diagnostic")


def device_entities(device: dict[str, Any]) -> list[tuple[EntitySpec, Any]]:
    """Return (spec, raw value) for every supported state of a device."""
    model_name = str(device.get("modelName") or "")
    result: list[tuple[EntitySpec, Any]] = []
    if "online" in device:
        result.append((ONLINE_SPEC, device["online"]))
    features = device.get("features") or {}
    for feature, fdata in features.items():
        states = (fdata or {}).get("states") or {}
        for state, sdata in states.items():
            if not isinstance(sdata, dict) or "value" not in sdata:
                continue
            spec = entity_spec(model_name, feature, state)
            if spec is None:
                _LOGGER.debug("Ignoring unsupported state %s.%s on %s", feature, state, model_name)
                continue
            result.append((spec, sdata["value"]))
    return result


def device_state_payload(device: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON state document published for a device."""
    payload: dict[str, Any] = {}
    for spec, raw in device_entities(device):
        payload[spec.key] = spec.convert(raw)
    return payload


def device_has_problem(device: dict[str, Any], feature: str, states: tuple[str, ...]) -> bool:
    fstates = ((device.get("features") or {}).get(feature) or {}).get("states") or {}
    return any(to_bool((fstates.get(s) or {}).get("value")) for s in states)
