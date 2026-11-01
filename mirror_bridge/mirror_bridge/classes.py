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
    without anyone editing anything. `min_value` / `max_value` are the fallback
    for a range the entity does not describe at all - a light's brightness is
    0-255 by definition, not by report.
    """
    service: str                    # "climate.set_temperature"
    data_field: str                 # "temperature"
    kind: str                       # "onoff" | "number" | "enum" | "press" | "datetime" | "xy" | "relay"
    topic_key: str = "command_topic"  # discovery field carrying this topic
    enum_attr: str | None = None    # attribute listing the allowed values
    min_attr: str | None = None     # attribute holding the lower bound
    max_attr: str | None = None     # attribute holding the upper bound
    min_value: float | None = None  # fixed lower bound, when no attribute has it
    max_value: float | None = None  # fixed upper bound, likewise
    as_int: bool = False            # round before calling the service


@dataclass(frozen=True)
class Spec:
    component: str                       # MQTT platform on the mirror
    config: dict = field(default_factory=dict)      # extra discovery fields
    attributes: tuple = ()               # attributes published as own topics
    # discovery field -> attribute whose topic it points at, for the platforms
    # that read parts of their state from somewhere other than `state_topic`.
    state_map: dict = field(default_factory=dict)
    commands: dict = field(default_factory=dict)    # capability -> Command
    # Publish every attribute as one JSON object on the "attributes" topic,
    # for the MQTT platform's json_attributes_topic.
    json_attributes: bool = False


ONOFF = Command(service="homeassistant.turn_on", data_field="", kind="onoff")

# (has_date, has_time) -> the set_datetime field and the text the mirror accepts.
# Seconds are optional on the way in; the helper reports them.
DATETIME_FORMS = {
    (True, True): ("datetime", r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$"),
    (True, False): ("date", r"^\d{4}-\d{2}-\d{2}$"),
    (False, True): ("time", r"^\d{2}:\d{2}(:\d{2})?$"),
}


def _carry(attrs, *names):
    """Copy presentation attributes through to the discovery payload."""
    return {n: attrs[n] for n in names if attrs.get(n) is not None}


def describe(entity_id: str, state: dict, writable: bool,
             relay: str | None = None) -> Spec | None:
    """Map a Home Assistant entity to its representation on the mirror.

    `relay` is the topic the add-on's `relays` option names for this entity,
    if any (see the sensor class).

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
        if writable and relay:
            # A relay: a sensor that is another program's interface - its
            # attributes the state that program publishes, its command a JSON
            # object passed on to that program's own topic. Live only with both
            # the writable label and the add-on's `relays` option naming it;
            # the program behind it checks every command itself.
            return Spec(
                component="sensor",
                config={**common, **_carry(attrs, "unit_of_measurement")},
                state_map={"json_attributes_topic": "attributes"},
                commands={"command": Command(service="", data_field="",
                                             kind="relay")},
                json_attributes=True,
            )
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
                                topic_key="mode_command_topic",
                                enum_attr="hvac_modes"),
                "temperature": Command(service="climate.set_temperature",
                                       data_field="temperature", kind="number",
                                       topic_key="temperature_command_topic",
                                       min_attr="min_temp", max_attr="max_temp"),
            }
        return Spec(component="climate", config=cfg,
                    attributes=("current_temperature", "temperature", "hvac_action"),
                    state_map={
                        "current_temperature_topic": "current_temperature",
                        "temperature_state_topic": "temperature",
                        "action_topic": "hvac_action",
                    },
                    commands=commands)

    # --- buttons -------------------------------------------------------------
    # Only a writable button is mirrored: its state is the time it was last
    # pressed, which nobody reads, and a button you cannot press is nothing.
    if domain == "button":
        if not writable:
            return None
        return Spec(component="button", config={**common, "payload_press": "PRESS"},
                    commands={"press": Command(service="button.press", data_field="",
                                               kind="press")})

    # --- date and time helpers -----------------------------------------------
    # The MQTT platforms have no date or time entity, so a writable one is a
    # text entity held to the helper's own format by a pattern, and checked
    # again on the way in. The state is the helper's own text
    # ("2026-10-25 18:45:00"), which already matches.
    if domain == "input_datetime":
        if not writable:
            return Spec(component="sensor", config=common)
        has_date, has_time = attrs.get("has_date", True), attrs.get("has_time", True)
        field_name, pattern = DATETIME_FORMS[(bool(has_date), bool(has_time))]
        return Spec(component="text",
                    config={**common, "pattern": pattern, "min": 5, "max": 19},
                    commands={"set": Command(service="input_datetime.set_datetime",
                                             data_field=field_name, kind="datetime")})

    # --- lights --------------------------------------------------------------
    # A read-only light stays a sensor: the MQTT light platform requires a
    # command topic, so there is no such thing as a light you cannot switch.
    # What a writable one carries is decided per entity from
    # `supported_color_modes`, so a plain on/off bulb does not get a brightness
    # slider it would ignore. Colour goes as xy whatever the source's own mode
    # (xy is what Hue speaks, and Home Assistant reports `xy_color` for every
    # colour light); effects as the source's own `effect_list`.
    if domain == "light":
        if not writable:
            return Spec(component="sensor", config=common,
                        attributes=("brightness",))

        modes = set(attrs.get("supported_color_modes") or [])
        dimmable = bool(modes - {"onoff"})
        has_color_temp = "color_temp" in modes
        has_colour = bool(modes & {"xy", "hs", "rgb", "rgbw", "rgbww"})

        cfg = {**common, "payload_on": "on", "payload_off": "off",
               "optimistic": False}
        attributes = []
        state_map = {}
        commands = {"set": Command(service="light.turn_on", data_field="",
                                   kind="onoff")}

        if dimmable:
            cfg["brightness_scale"] = 255
            attributes.append("brightness")
            state_map["brightness_state_topic"] = "brightness"
            commands["brightness"] = Command(
                service="light.turn_on", data_field="brightness", kind="number",
                topic_key="brightness_command_topic",
                min_value=0, max_value=255, as_int=True)

        if has_color_temp:
            # Kelvin end to end. The platform defaults to mireds and converts,
            # which would mean publishing a unit Home Assistant itself stopped
            # using - `color_temp_kelvin` is the attribute the source reports.
            cfg["color_temp_kelvin"] = True
            cfg["min_kelvin"] = attrs.get("min_color_temp_kelvin", 2000)
            cfg["max_kelvin"] = attrs.get("max_color_temp_kelvin", 6535)
            attributes.append("color_temp_kelvin")
            state_map["color_temp_state_topic"] = "color_temp_kelvin"
            commands["color_temp"] = Command(
                service="light.turn_on", data_field="color_temp_kelvin",
                kind="number", topic_key="color_temp_command_topic",
                min_attr="min_color_temp_kelvin", max_attr="max_color_temp_kelvin",
                as_int=True)

        if has_colour:
            attributes.append("xy_color")
            state_map["xy_state_topic"] = "xy_color"
            commands["xy"] = Command(service="light.turn_on", data_field="xy_color",
                                     kind="xy", topic_key="xy_command_topic")
            if has_color_temp and attrs.get("color_mode") == "color_temp":
                # The MQTT light takes its colour mode from whichever of the two
                # it heard last, so the one the source is in goes last.
                attributes.remove("color_temp_kelvin")
                attributes.append("color_temp_kelvin")

        effects = attrs.get("effect_list")
        if effects:
            cfg["effect_list"] = list(effects)
            attributes.append("effect")
            state_map["effect_state_topic"] = "effect"
            commands["effect"] = Command(service="light.turn_on", data_field="effect",
                                         kind="enum", topic_key="effect_command_topic",
                                         enum_attr="effect_list")

        return Spec(component="light", config=cfg, attributes=tuple(attributes),
                    state_map=state_map, commands=commands)

    # --- scenes --------------------------------------------------------------
    # Like a button: only a writable scene is mirrored, and it has no state
    # worth showing. The MQTT scene's payload is PRESS, so the button's check
    # serves both.
    if domain == "scene":
        if not writable:
            return None
        return Spec(component="scene", config={**common, "payload_on": "PRESS"},
                    commands={"activate": Command(service="scene.turn_on", data_field="",
                                                  kind="press")})

    return None


# Capabilities the mirror is allowed to ask for, per component. The dispatcher
# checks this before anything else, so a command naming an unknown capability is
# rejected without ever reaching a service call.
ALLOWED = {
    "switch": {"set"},
    "climate": {"mode", "temperature"},
    "light": {"set", "brightness", "color_temp", "xy", "effect"},
    "button": {"press"},
    "text": {"set"},
    "scene": {"activate"},
    "sensor": {"command"},
}
