"""How each Home Assistant domain becomes an entity on the mirror.

This is the only file that needs touching to support a new *class* of device.
Everything else in the add-on is generic: membership comes from a label, and
every per-entity detail (unit, device class, icon, temperature limits, available
modes) is read from the source entity's own attributes at publish time.

Because this add-on publishes the state itself, attribute payloads are plain
text rather than JSON. That is deliberate: `mqtt_statestream` publishes string
attributes JSON-quoted (`"idle"`) while numbers arrive bare, which forces a
decode template on the consuming side. Publishing plain values removes that
whole class of mismatch.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Command:
    """A writable capability, and how to validate it before acting.

    Validation deliberately reads its bounds from the live entity rather than
    from configuration, so a device that reports different limits is handled
    without anyone editing anything.
    """
    service: str                    # "climate.set_temperature"
    data_field: str                 # "temperature"
    kind: str                       # "onoff" | "number" | "enum"
    enum_attr: str | None = None    # attribute listing the allowed values
    min_attr: str | None = None     # attribute holding the lower bound
    max_attr: str | None = None     # attribute holding the upper bound


@dataclass(frozen=True)
class Spec:
    component: str                       # MQTT platform on the mirror
    config: dict = field(default_factory=dict)      # extra discovery fields
    attributes: tuple = ()               # attributes published as own topics
    commands: dict = field(default_factory=dict)    # capability -> Command


ONOFF = Command(service="homeassistant.turn_on", data_field="", kind="onoff")


def _carry(attrs, *names):
    """Copy presentation attributes through to the discovery payload."""
    return {n: attrs[n] for n in names if attrs.get(n) is not None}


def describe(entity_id: str, state: dict, writable: bool) -> Spec | None:
    """Map a Home Assistant entity to its representation on the mirror.

    Returns None for a domain this add-on does not know how to mirror, which is
    logged once rather than failing - an unsupported label is a mistake to
    surface, not a crash.
    """
    domain = entity_id.split(".", 1)[0]
    attrs = state.get("attributes", {}) or {}
    common = _carry(attrs, "icon")

    # --- switchable booleans -------------------------------------------------
    if domain in ("input_boolean", "switch"):
        if writable:
            return Spec(
                component="switch",
                config={**common, "payload_on": "on", "payload_off": "off",
                        "state_on": "on", "state_off": "off", "optimistic": False},
                commands={"set": ONOFF},
            )
        return Spec(component="binary_sensor",
                    config={**common, "payload_on": "on", "payload_off": "off"})

    if domain == "binary_sensor":
        return Spec(component="binary_sensor",
                    config={**common, **_carry(attrs, "device_class"),
                            "payload_on": "on", "payload_off": "off"})

    # --- measurements --------------------------------------------------------
    if domain == "sensor":
        return Spec(
            component="sensor",
            config={**common, **_carry(attrs, "device_class", "state_class",
                                       "unit_of_measurement")},
        )

    # --- thermostats ---------------------------------------------------------
    if domain == "climate":
        cfg = {
            **common,
            "modes": attrs.get("hvac_modes", ["off", "heat"]),
            "min_temp": attrs.get("min_temp", 5),
            "max_temp": attrs.get("max_temp", 35),
            "temp_step": attrs.get("target_temp_step", 0.5),
        }
        commands = {}
        if writable:
            commands = {
                "mode": Command(service="climate.set_hvac_mode",
                                data_field="hvac_mode", kind="enum",
                                enum_attr="hvac_modes"),
                "temperature": Command(service="climate.set_temperature",
                                       data_field="temperature", kind="number",
                                       min_attr="min_temp", max_attr="max_temp"),
            }
        return Spec(component="climate", config=cfg,
                    attributes=("current_temperature", "temperature", "hvac_action"),
                    commands=commands)

    # --- lights --------------------------------------------------------------
    # Mirrored read-only as a sensor unless writable, because a light's useful
    # value is often its brightness rather than on/off. Revisit when a writable
    # light is actually wanted.
    if domain == "light":
        return Spec(component="sensor", config=common, attributes=("brightness",))

    return None


# Capabilities the mirror is allowed to ask for, per component. The dispatcher
# checks this before anything else, so a command naming an unknown capability is
# rejected without ever reaching a service call.
ALLOWED = {
    "switch": {"set"},
    "climate": {"mode", "temperature"},
}
