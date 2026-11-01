# Changelog

## 0.8.0

- **`input_number` helpers**: writable, an MQTT number with the helper's own
  min, max, step, mode and unit, a command clamped to the range before
  `input_number.set_value`; read-only, a sensor.
- **Timers**: always read-only, a sensor whose state is idle, active or paused,
  with the timer's attributes (duration, remaining, finishes_at) as JSON.
- **Scripts**: writable only, an MQTT button whose press calls
  `script.turn_on`.

## 0.7.0

- **Relays**: a new option, `relays`, a list of `"sensor.x=topic"`. A sensor
  named there that also carries the writable label is another program's
  interface on the mirror: all its attributes go out as one JSON object
  (`json_attributes_topic`), and its command topic takes a JSON object of at
  most 16 KB, published unchanged and unretained on `topic`. Only the shape is
  checked; the program behind the relay checks the content. A topic with an
  MQTT wildcard is dropped. Each relayed command is logged with its `op`.

## 0.6.0

- **Scenes** are mirrored when writable, as MQTT scenes whose activation
  calls `scene.turn_on` on the source. A read-only scene is not mirrored.
- **Light colour**: a colour light (xy, hs or rgb) gets an xy colour command
  and state, `"x,y"` on the wire, checked as two values in 0-1 before
  `light.turn_on` is called with `xy_color`. The attribute among colour
  temperature and colour that matches the source's `color_mode` is published
  last, since the MQTT light takes its mode from the last it heard. This
  reverses 0.4.0's choice not to mirror colour.
- **Light effects**: a light with an `effect_list` gets it on the mirror, and
  an effect command accepted only if the source lists it.
- An attribute that is a list or tuple is published as comma-separated text.

## 0.5.0

- **Buttons** are mirrored when writable, as MQTT buttons whose press calls
  `button.press` on the source. A read-only button is not mirrored: its state
  is only the time it was last pressed.
- **`input_datetime` helpers** are mirrored as text entities when writable,
  held to the helper's own format by a pattern (`YYYY-MM-DD HH:MM`, seconds
  optional; a date-only or time-only helper takes just that part). A command is
  parsed as a real date and time before `input_datetime.set_datetime` is
  called, so 2026-02-30 is refused. Read-only, a helper is a sensor.
- Tests for both, runnable with pytest or `python3 tests/test_classes.py`.

## 0.4.0

- **Lights are mirrored as lights when writable**, with on/off, brightness and
  colour temperature. What a given light carries is decided per entity from its
  `supported_color_modes`, so an on/off bulb gets no brightness slider and a
  bulb without `color_temp` gets no temperature control. Colour (xy/hs) is
  deliberately not mirrored. Colour temperature is published in kelvin
  (`color_temp_kelvin: true`), matching the attribute the source reports, rather
  than letting the MQTT platform convert to mireds. A read-only light stays a
  sensor, because the MQTT light platform requires a command topic.
- `Spec` gained `state_map` and `Command` gained `topic_key`, so a platform that
  reads part of its state from its own topic - a thermostat's current
  temperature, a light's brightness - declares that in `classes.py` instead of
  in a branch in `bridge.py`. The climate special case in `_discovery` shrank to
  the one genuinely odd thing about it: its `state_topic` is a mode topic.
- `Command` also gained `min_value` / `max_value` for a range the entity does
  not describe at all (brightness is 0-255 by definition, not by report) and
  `as_int` for services that want a whole number. An attribute-derived bound
  still wins over a fixed one.
- On/off commands now take their domain from the command's own service instead
  of always calling `homeassistant.turn_on`.

## 0.3.1

- Dockerfile defaults `BUILD_FROM`, since Supervisor 2026.04.0+ no longer passes
  it automatically and the add-on should build with or without `build.yaml`.
  First version published to this repository.

## 0.3.0

- Names reproduce the source's own structure instead of doubling up. Home
  Assistant composes a display name from a device plus the entity's short name,
  and the source already works that way; sending the already-composed
  `friendly_name` *and* attaching a synthetic device made it compose twice,
  giving `sensor.pi_mirror_radiator_gang_2_etasje_temperature`. Each entity is
  now published under a device derived from its source device, with
  `has_entity_name` and the short `original_name`. Entities with no device on
  the source stay loose and keep their own name.
- Note: an existing entity does not move its entity_id when renamed. Home
  Assistant restores the previous id when the same `unique_id` reappears, so
  moving one requires `config/entity_registry/update` with `new_entity_id`.

## 0.2.1 - 0.2.3

- `object_id` in the discovery payload, tying the mirror's entity_id to the
  source entity rather than to the display name.
- Tried and reverted `has_entity_name: false`: with a device block present,
  Home Assistant composes the name regardless and the flag has no effect.

## 0.2.0

- Reconnection on both sides. The websocket is re-established with backoff when
  Home Assistant restarts, re-reading membership and re-subscribing; previously
  the run loop simply ended.
- Re-announce every mirrored entity after an MQTT reconnect. A client that only
  publishes its state at startup is silently wrong for as long as the broker
  remembers a stale retained value - the failure mode observed in another add-on
  on the same broker.

## 0.1.0

Initial version. Add-on packaging, the domain-to-mirror class table, and the
runtime: label-driven membership, discovery publishing, retraction on removal,
and a command path validated against the entity's own attributes.

Fixed during the first run: `on_connect` logged success without checking paho's
reason code, which hid an authentication-rejection reconnect loop behind five
cheerful "connected" lines. Added explicit `mqtt_user`/`mqtt_password` options
because `services: ["mqtt:need"]` is unusable while the broker defines a manual
login colliding with the Supervisor's service account name.
